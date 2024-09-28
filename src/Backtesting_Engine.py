import warnings
import pandas as pd

warnings.simplefilter(action='ignore', category=FutureWarning)


def handle_multiple_orders(price_data, signal_datetime, side, num_orders, pct_margin_orders, pct_order_range,
                           entry_time_offset, percentage_change, open_order_elimination, tp_list, sl_list):
    def log_order(output_data, order_num, status, entry_price, exec_time, tp_price, sl_price, exit_reason, exit_time,
                  duration):
        """Log details of each order."""
        output_data.append({
            'Order Number': order_num,
            'Order Status': status,
            'Entry Price': entry_price,
            'Execution Time': exec_time,
            'TP Price': tp_price,
            'SL Price': sl_price,
            'Exit Reason': exit_reason,
            'Exit Time': exit_time,
            'Duration': duration
        })
        print(f"Order {order_num} logged: Status={status}, Entry Price={entry_price}, Execution Time={exec_time}, "
              f"TP={tp_price}, SL={sl_price}, Exit Reason={exit_reason}, Exit Time={exit_time}, Duration={duration}")

    def calculate_tp_sl(entry_price, tp_pct, sl_pct, side):
        """Calculate take profit and stop loss prices based on the entry price and side."""
        tp_price = entry_price * (1 + tp_pct if side == 'Buy' else 1 - tp_pct)
        sl_price = entry_price * (1 - sl_pct if side == 'Buy' else 1 + sl_pct)
        print(f"Calculated TP={tp_price}, SL={sl_price} for Entry Price={entry_price}, Side={side}")
        return tp_price, sl_price

    def process_order(price_data, entry_datetime, entry_price, side, tp_pct, sl_pct):
        """Process a single order and determine the exit point."""
        print(f"Processing order with Entry Price={entry_price} at {entry_datetime} for Side={side}")
        tp_price, sl_price = calculate_tp_sl(entry_price, tp_pct, sl_pct, side)
        exit_reason, exit_datetime, exit_price = check_position_exit(price_data, entry_datetime, tp_price, sl_price,
                                                                     side, entry_price)
        print(f"Exit Reason={exit_reason}, Exit Date={exit_datetime}, Exit Price={exit_price}")
        return exit_reason, exit_datetime, exit_price, tp_price, sl_price

    def check_position_exit(price_data, entry_datetime, tp_price, sl_price, side, entry_price):
        """Check if the position hits take profit, stop loss, or exits early due to events."""
        print(f"Checking for position exit: TP={tp_price}, SL={sl_price}, Side={side}")

        # Track initial exit reason and datetime based on TP/SL
        exit_reason = None
        exit_datetime = None
        exit_price = None

        # Loop through each price data row starting from entry time
        for price_time, price_row in price_data.loc[entry_datetime:].iterrows():

            # Check for Take Profit and Stop Loss first
            if side == 'Buy':
                if price_row['High'] >= tp_price:
                    exit_reason = 'Take Profit'
                    exit_datetime = price_time
                    exit_price = tp_price
                    break  # Exit loop once TP is hit
                elif price_row['Low'] <= sl_price:
                    exit_reason = 'Stop Loss'
                    exit_datetime = price_time
                    exit_price = sl_price
                    break  # Exit loop once SL is hit
            else:  # Sell side
                if price_row['Low'] <= tp_price:
                    exit_reason = 'Take Profit'
                    exit_datetime = price_time
                    exit_price = tp_price
                    break  # Exit loop once TP is hit
                elif price_row['High'] >= sl_price:
                    exit_reason = 'Stop Loss'
                    exit_datetime = price_time
                    exit_price = sl_price
                    break  # Exit loop once SL is hit

        # If no exit was triggered by TP/SL, return None
        if exit_reason is None:
            return None, None, None

        # Now check if any event occurred between the entry and exit time
        for price_time, price_row in price_data.loc[entry_datetime:exit_datetime].iterrows():

            if 'event_datetimes' in price_row and pd.notna(price_row['event_datetimes']):
                event_times = [pd.to_datetime(e.strip()) for e in str(price_row['event_datetimes']).split(',')]
                for event_datetime in event_times:
                    if entry_datetime < event_datetime < exit_datetime:
                        # Set the exit time to 10 minutes before the event
                        event_exit_datetime = event_datetime - pd.Timedelta(minutes=10)

                        # Ensure the exit datetime exists in the price data
                        if event_exit_datetime in price_data.index:
                            event_exit_price = price_data.at[event_exit_datetime, 'Open']

                            # Determine if this is a profitable or loss exit based on the side
                            if (side == 'Buy' and event_exit_price > entry_price) or (
                                    side == 'Sell' and event_exit_price < entry_price):
                                print(
                                    f"Exiting early due to event: Exit Price={event_exit_price}, Exit Time={event_exit_datetime}")
                                return 'Ended before event with profit', event_exit_datetime, event_exit_price
                            else:
                                print(
                                    f"Exiting early due to event with loss: Exit Price={event_exit_price}, Exit Time={event_exit_datetime}")
                                return 'Ended before event with loss', event_exit_datetime, event_exit_price

        # Return the original TP/SL exit if no earlier event exit occurred
        return exit_reason, exit_datetime, exit_price

    orders = []
    output_data = []
    total_margin_used = 0
    total_invested = 0
    intended_total_margin_used = 0

    # First order entry
    print("Starting first order entry...")
    entry_datetime, entry_price, entry_duration = determine_entry(price_data, signal_datetime, percentage_change, side,
                                                                  open_order_elimination, entry_time_offset)

    if entry_datetime is None:
        print("First order not filled. No entry detected.")
        log_order(output_data, 1, 'Unfilled', None, None, None, None, 'No entry', None, None)
        return output_data, None

    # Append first order
    print(f"First order entered at {entry_datetime} with price {entry_price}")
    orders.append((entry_datetime, entry_price, pct_margin_orders[0], entry_duration))
    total_invested += pct_margin_orders[0] * entry_price
    total_margin_used += pct_margin_orders[0]
    average_entry_price = entry_price

    # Process the first order
    exit_reason, exit_datetime, exit_price, tp_price, sl_price = process_order(price_data, entry_datetime, entry_price,
                                                                               side, tp_list[0], sl_list[0])
    duration = (exit_datetime - entry_datetime).total_seconds() / 60 if exit_datetime else None
    log_order(output_data, 1, 'Filled', entry_price, entry_datetime, tp_price, sl_price, exit_reason, exit_datetime,
              duration)

    previous_exit_datetime = exit_datetime

    # Process subsequent orders
    for i in range(1, num_orders):
        print(f"Processing order {i + 1}...")
        margin_percentage = pct_margin_orders[i]
        intended_total_margin_used += margin_percentage

        if intended_total_margin_used > 1.0:
            raise ValueError("Total margin percentage exceeds 100%")

        previous_order_price = orders[-1][1]
        adjusted_entry_price = previous_order_price * (
                    1 - pct_order_range[i - 1]) if side == 'Buy' else previous_order_price * (
                    1 + pct_order_range[i - 1])

        entry_datetime = None
        valid_order = False

        # Check for valid order entry
        for price_time, price_row in price_data.loc[orders[-1][0]:].iterrows():
            if (side == 'Buy' and price_row['Low'] <= adjusted_entry_price) or (
                    side == 'Sell' and price_row['High'] >= adjusted_entry_price):
                valid_order = True
                entry_datetime = price_time
                entry_duration = (entry_datetime - signal_datetime).total_seconds() / 60

                # Check if the previous order's exit was before this new entry
                if previous_exit_datetime and entry_datetime <= previous_exit_datetime:
                    total_invested += margin_percentage * adjusted_entry_price
                    total_margin_used += margin_percentage
                    average_entry_price = total_invested / total_margin_used

                    # Process the order
                    exit_reason, exit_datetime, exit_price, tp_price, sl_price = process_order(price_data,
                                                                                               entry_datetime,
                                                                                               adjusted_entry_price,
                                                                                               side, tp_list[i],
                                                                                               sl_list[i])
                    previous_exit_datetime = exit_datetime
                    break
                elif previous_exit_datetime and entry_datetime > previous_exit_datetime:
                    # If the previous order has exited before this entry, skip further calculations
                    log_order(output_data, i, 'Closed', average_entry_price, previous_exit_datetime, tp_price, sl_price,
                              exit_reason, previous_exit_datetime, None)
                    tp_price, sl_price = calculate_tp_sl(adjusted_entry_price, tp_list[i], sl_list[i], side)
                    log_order(output_data, i + 1, 'Not Filled', adjusted_entry_price, None, tp_price, sl_price,
                              f'Not Filled because previous position closed at {previous_exit_datetime}.', None, None)
                    print(f"Order {i + 1} not filled because the previous order closed at {previous_exit_datetime}")

                    return output_data, average_entry_price
                break

        # If a valid order was found and processed
        if valid_order:
            print(f"Order {i + 1} filled at {entry_datetime} with price {adjusted_entry_price}")

            orders.append((entry_datetime, adjusted_entry_price, margin_percentage, entry_duration))
            total_invested += margin_percentage * adjusted_entry_price
            total_margin_used += margin_percentage
        else:
            tp_price, sl_price = calculate_tp_sl(adjusted_entry_price, tp_list[i], sl_list[i], side)
            intended_total_margin_used -= margin_percentage
            log_order(output_data, i + 1, 'Not Filled', adjusted_entry_price, None, tp_price, sl_price, 'Not Filled',
                      None, None)
            print(f"Order {i + 1} not filled")

        # Ensure that each order's exit is properly logged
        if exit_reason:
            log_order(output_data, i + 1, 'Filled', adjusted_entry_price, entry_datetime, tp_price, sl_price,
                      exit_reason, exit_datetime, duration)
        else:
            log_order(output_data, i + 1, 'Not Filled', None, None, None, None, 'No valid entry', None, None)

    return output_data, average_entry_price


def backtest_trades(price_data, signal_data, leverage, num_orders=None, pct_margin_orders=None, pct_order_range=None,
                    tp_list=None, sl_list=None, entry_time_offset=None, percentage_change=None,
                    open_order_elimination=None, ignore_time_interval_before=None, ignore_time_interval_after=None):
    output_data = pd.DataFrame(columns=[
        'Order ID', 'Position ID', 'Signal Datetime', 'Order Side', 'Order Status', 'Order Execution Time',
        'Order Entry Price', 'Quantity',
        'TP Price', 'SL Price', 'Order exit', 'Order Filled Value', 'Order Duration', 'Order Exit Datetime',
        'Order Realized PnL',
        'Order NAV', 'Order ROI', 'Position Entry Average Price', 'Position Filled Value', 'Position Realized PnL',
        'Position NAV', 'Position ROI'
    ])

    initial_margin = 100000
    position_id_counter = 1  # Unique Position ID tracker
    order_id = 0  # Unique Order ID tracker
    exit_datetimes = []  # Will store tuples (exit_datetime, side)

    for i, row in signal_data.iterrows():
        signal_datetime = row['Datetime']
        signal_value = row['Signal']

        if signal_value == 0:
            continue

        side = 'Buy' if signal_value > 0 else 'Sell'

        # Ignore signals if there are open trades
        exit_datetimes.sort(
            key=lambda x: (pd.to_datetime(x[0], errors='coerce') is pd.NaT, pd.to_datetime(x[0], errors='coerce')))
        ignore_signal = False
        reason = ''

        if exit_datetimes:
            # Convert signal_datetime to Timestamp to ensure proper comparison
            signal_datetime = pd.to_datetime(signal_datetime)
            # Filter and compare exit datetimes, ensuring valid Timestamps
            later_exits = [ed for ed in exit_datetimes if
                           ed[0] is not None and pd.to_datetime(ed[0], errors='coerce') > signal_datetime]
            if len(later_exits) >= 2:
                reason = '2 open trades'
                ignore_signal = True
            elif len(later_exits) == 1 and later_exits[-1][1] == side:
                reason = 'One open trade with the same side'
                ignore_signal = True

        # Ignore signals around events
        if 'event_datetimes' in row and pd.notna(row['event_datetimes']):
            event_times = [pd.to_datetime(e.strip()) for e in str(row['event_datetimes']).split(',')]
            ignore_signal = any(
                event_datetime - pd.Timedelta(minutes=ignore_time_interval_before) <= signal_datetime <=
                event_datetime + pd.Timedelta(minutes=ignore_time_interval_after)
                for event_datetime in event_times
            )
            if ignore_signal:
                reason = 'Signal around event'

        # If signal is ignored, log it and continue
        if ignore_signal:
            order_id += 1
            new_row = pd.DataFrame([{
                'Order ID': order_id,
                'Position ID': None,
                'Signal Datetime': signal_datetime,
                'Order Side': side,
                'Order Status': 'Ignored',
                'Order Execution Time': None,
                'Order Entry Price': None,
                'Quantity': None,
                'TP Price': None,
                'SL Price': None,
                'Order exit': reason,
                'Order Filled Value': None,
                'Order Duration': None,
                'Order Exit Datetime': None,
                'Order Realized PnL': None,
                'Order NAV': None,
                'Order ROI': None,
                'Position Entry Average Price': None,
                'Position Filled Value': None,
                'Position Realized PnL': 0,
                'Position NAV': None,
                'Position ROI': None
            }])
            output_data = pd.concat([output_data, new_row], ignore_index=True)
            continue

        # Call the handle_multiple_orders function to execute the orders
        result, average_entry_price = handle_multiple_orders(
            price_data, signal_datetime, side, num_orders, pct_margin_orders, pct_order_range,
            entry_time_offset, percentage_change, open_order_elimination, tp_list, sl_list
        )

        if result is None:
            continue

        total_filled_quantity = 0
        position_realized_pnl = 0
        filled_orders = []  # Track filled orders
        cumulative_filled_value = 0
        cumulative_available_margin = 0
        position_available_margin = 0
        # Process each order and log the results
        for order_index, order in enumerate(result):
            order_status = order['Order Status']

            # If the order is not filled, skip PnL and margin calculations but log the order
            if order_status == 'Not Filled':
                available_margin = initial_margin * pct_margin_orders[order['Order Number'] - 1]
                entry_price = order['Entry Price']
                quantity = (available_margin * leverage) / entry_price
                order_id += 1
                new_order_row = pd.DataFrame([{
                    'Order ID': order_id,
                    'Position ID': None,
                    'Signal Datetime': signal_datetime,
                    'Order Side': side,
                    'Order Status': order_status,
                    'Order Execution Time': None,
                    'Order Entry Price': order['Entry Price'],
                    'Quantity': quantity,
                    'TP Price': order['TP Price'],
                    'SL Price': order['SL Price'],
                    'Order exit': order['Exit Reason'],
                    'Order Filled Value': None,
                    'Order Duration': None,
                    'Order Exit Datetime': None,
                    'Order Realized PnL': None,
                    'Order NAV': None,
                    'Order ROI': None,
                    'Position Entry Average Price': None,
                    'Position Filled Value': None,
                    'Position Realized PnL': 0,
                    'Position NAV': None,
                    'Position ROI': None
                }])
                output_data = pd.concat([output_data, new_order_row], ignore_index=True)
                continue  # Skip further calculations for "Not Filled" orders

            # If the order is filled, proceed with the PnL and margin calculations
            if order_status == 'Filled':
                # Reduce margin for order fee
                available_margin = initial_margin * pct_margin_orders[order['Order Number'] - 1]
                position_available_margin += available_margin
                current_margin = available_margin
                current_margin *= (1 - 0.0002)
                entry_price = order['Entry Price']
                quantity = (current_margin * leverage) / entry_price  # Leverage-based quantity calculation

                filled_value = entry_price * quantity
                cumulative_filled_value += filled_value
                total_filled_quantity += quantity

                filled_orders.append(order)  # Track this order as filled

                # Determine exit reason and price
                exit_reason = order['Exit Reason']
                if exit_reason == 'Take Profit':
                    exit_price = order['TP Price']
                elif exit_reason == 'Stop Loss':
                    exit_price = order['SL Price']
                elif exit_reason == 'Ended before event with profit':
                    exit_price = order['SL Price']
                elif exit_reason == 'Ended before event with loss':
                    exit_price = order['TP Price']

                # Calculate order PnL
                order_pnl = (exit_price - entry_price) * quantity if side == 'Buy' else (
                                                                                                    entry_price - exit_price) * quantity
                position_realized_pnl += order_pnl

                # Update NAV and apply margin changes based on exit
                if exit_reason == 'Take Profit':
                    current_margin *= (1 + tp_list[order_index])
                    current_margin *= (1 - 0.0005)
                elif exit_reason == 'Stop Loss':
                    current_margin *= (1 - sl_list[order_index])
                    current_margin *= (1 - 0.0005)
                elif exit_reason in ['Ended before event with profit', 'Ended before event with loss']:
                    # Event-based exit logic: calculate percentage change and update margin
                    if (side == 'Buy' and exit_price > entry_price) or (side == 'Sell' and exit_price < entry_price):
                        # Exit with profit
                        pct_change = (exit_price - entry_price) / entry_price if side == 'Buy' else (
                                                                                                                entry_price - exit_price) / entry_price
                        current_margin = current_margin * (1 + pct_change)
                        current_margin *= (1 - 0.0005)
                    else:
                        # Exit with loss
                        pct_change = (entry_price - exit_price) / entry_price if side == 'Buy' else (
                                                                                                                exit_price - entry_price) / entry_price
                        current_margin = current_margin * (1 - pct_change)
                        current_margin *= (1 - 0.0005)

                cumulative_available_margin += current_margin
                # Increment unique order ID
                order_id += 1

                # Check if the order's exit time is not None
                order_exit_datetime = order['Exit Time']

                # Only calculate the duration if the exit time is available
                order_exit_datetime = pd.to_datetime(order_exit_datetime)

                # Only calculate the duration if the exit time is available
                if order_exit_datetime is not None:
                    order_duration = pd.to_timedelta(
                        (order_exit_datetime - order['Execution Time']).total_seconds() / 60,
                        unit='m')  # Duration in minutes
                else:
                    order_duration = None
                # Append order information to output
                new_order_row = pd.DataFrame([{
                    'Order ID': order_id,
                    'Position ID': position_id_counter,
                    'Signal Datetime': signal_datetime,
                    'Order Side': side,
                    'Order Status': order_status,
                    'Order Execution Time': order['Execution Time'],
                    'Order Entry Price': entry_price,
                    'Quantity': quantity,
                    'TP Price': order['TP Price'],
                    'SL Price': order['SL Price'],
                    'Order exit': exit_reason,
                    'Order Filled Value': filled_value,
                    'Order Duration': order_duration,
                    'Order Exit Datetime': order_exit_datetime,
                    'Order Realized PnL': order_pnl,
                    'Order NAV': current_margin,
                    'Order ROI': ((current_margin - available_margin) / available_margin) * 100,
                    'Position Entry Average Price': average_entry_price,
                    'Position Filled Value': cumulative_filled_value,
                    'Position Realized PnL': position_realized_pnl,
                    'Position NAV': cumulative_available_margin,
                    'Position ROI': ((
                                                 cumulative_available_margin - position_available_margin) / position_available_margin) * 100
                }])

                output_data = pd.concat([output_data, new_order_row], ignore_index=True)

        # Log exit time for the position after TP/SL exit
        if filled_orders:
            exit_datetimes.append((filled_orders[-1]['Exit Time'], side))

        # Increment position ID for the next position
        position_id_counter += 1

    return output_data


# Helper functions
def format_duration(duration):
    seconds = duration.total_seconds()
    hours = int(seconds // 3600)
    minutes = int((seconds % 3600) // 60)
    seconds = int(seconds % 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}"


def check_tp_sl(price_data, entry_datetime, tp_price, sl_price, side):
    result = 0
    exit_datetime = None
    subsequent_prices = price_data.loc[entry_datetime:]

    for current_datetime, price_row in subsequent_prices.iterrows():
        if side == 'Buy':
            if price_row['High'] >= tp_price:
                result = 1
                exit_datetime = current_datetime
                break
            elif price_row['Low'] <= sl_price:
                result = -1
                exit_datetime = current_datetime
                break
        else:
            if price_row['Low'] <= tp_price:
                result = 1
                exit_datetime = current_datetime
                break
            elif price_row['High'] >= sl_price:
                result = -1
                exit_datetime = current_datetime
                break

    if exit_datetime:
        duration = exit_datetime - entry_datetime
        duration_str = format_duration(duration)
    else:
        duration_str = '00:00:00'

    return result, duration_str, exit_datetime


def determine_entry(price_data, signal_datetime, percentage_change, side, time_limit_minutes, entry_time_offset):
    adjusted_signal_datetime = signal_datetime + pd.Timedelta(minutes=entry_time_offset)
    if adjusted_signal_datetime not in price_data.index:
        return None, None, None

    adjusted_open_price = price_data.at[adjusted_signal_datetime, 'Open']
    percentage_change_price = adjusted_open_price * (
                1 - percentage_change) if side == 'Buy' else adjusted_open_price * (1 + percentage_change)

    time_limit = adjusted_signal_datetime + pd.Timedelta(minutes=time_limit_minutes)
    subsequent_prices = price_data.loc[adjusted_signal_datetime:time_limit]

    for current_datetime, price_row in subsequent_prices.iterrows():
        if side == 'Buy' and price_row['Low'] <= percentage_change_price:
            entry_price = percentage_change_price
            duration = current_datetime - adjusted_signal_datetime
            return current_datetime, entry_price, duration
        elif side == 'Sell' and price_row['High'] >= percentage_change_price:
            entry_price = percentage_change_price
            duration = current_datetime - adjusted_signal_datetime
            return current_datetime, entry_price, duration

    return None, None, None

price_data = pd.read_csv('E:\Signal Backtesting\Input\price_2023-06-01_to_2024-08-4_min.csv', parse_dates=['Datetime'],
                         index_col='Datetime')
# Re-load the signal data without setting the index
signal_data = pd.read_csv('E:\Signal Backtesting\Input\\filtered_signals_with_2023_2024_all_events_plus_one_hour.csv', parse_dates=['Datetime'])
# Define the month and year for which you want to perform the trades
month = 7  # January (you can change this to the desired month)
year = 2024  # You can change this to the desired year

# Filter the signal data for the specified month and year
signal_data = signal_data[(signal_data['Datetime'].dt.month == month) & (signal_data['Datetime'].dt.year == year)]

# Leverage
leverage = 1

# Number of orders per signal
num_orders = 2

# Margin allocation for each order (50%, 30%, 20%)
pct_margin_orders = [0.5, 0.5]

# Price range percentage for subsequent orders
pct_order_range = [0.01]  # E.g., second order is 1% below, third is 2% below first

# Take-Profit and Stop-Loss percentages
tp_list = [0.01, 0.011]  # TP for each order
sl_list = [0.01, 0.011]  # SL for each order

# Custom parameters (could be set to None or meaningful values based on your strategy)
entry_time_offset = 0
percentage_change = 0
open_order_elimination = 120

# Calling the function
output = backtest_trades(
    price_data=price_data,
    signal_data=signal_data,
    leverage=leverage,
    num_orders=num_orders,
    pct_margin_orders=pct_margin_orders,
    pct_order_range=pct_order_range,
    tp_list=tp_list,
    sl_list=sl_list,
    entry_time_offset=entry_time_offset,
    percentage_change=percentage_change,
    open_order_elimination=open_order_elimination,
    ignore_time_interval_before=1020,  # Ignore signals 15 minutes before an event
    ignore_time_interval_after=0    # Ignore signals 15 minutes after an event
)