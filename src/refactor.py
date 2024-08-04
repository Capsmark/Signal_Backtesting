import pandas as pd
import numpy as np
import random
import warnings
import logging


# Suppress all FutureWarnings
warnings.simplefilter(action='ignore', category=FutureWarning)


# Backtest trades function
def backtest_trades(price_data, signal_data, tp=None, sl=None, entry_time_offset=None,
                    percentage_change=None, time_limit_minutes=None, ignore_time_interval_before=None,
                    ignore_time_interval_after=None):
    output_data = pd.DataFrame(columns=[
        'Datetime', 'Side', 'Signal Open Price', 'Entry Price', 'TP Price', 'SL Price', 'Result', 'Duration',
        'Execution Latency', 'ROI', 'NAV', 'Ignore Reason'
    ])

    initial_margin = 100000
    current_margin = initial_margin
    exit_datetimes = []
    # ignore_intervals = []

    for i, row in signal_data.iterrows():
        signal_datetime = row['Datetime']
        signal_value = row['Signal']
        # btc_value = row['BTC']
        #
        # if btc_value == 'up':
        #     ignore_intervals.append((signal_datetime, signal_datetime + pd.Timedelta(days=2), 'Sell'))
        # elif btc_value == 'down':
        #     ignore_intervals.append((signal_datetime, signal_datetime + pd.Timedelta(days=2), 'Buy'))

        if signal_value == 0:
            continue
        elif signal_value > 0:
            side = 'Buy'
        else:
            side = 'Sell'

        adjusted_signal_datetime = signal_datetime + pd.Timedelta(minutes=entry_time_offset)

        signal_open_price = price_data.at[adjusted_signal_datetime, 'Open']

        # ignore_signal = False
        # reason = ''
        # for start, end, ignore_side in ignore_intervals:
        #     if start <= signal_datetime <= end and side == ignore_side:
        #         ignore_signal = True
        #         reason = f'Ignored due to BTC {ignore_side.lower()} signal from {start} to {end}'
        #         break
        #
        # if ignore_signal:
        #     new_row = pd.DataFrame([{
        #         'Datetime': signal_datetime,
        #         'Side': side,
        #         'Signal Open Price': signal_open_price,
        #         'Entry Price': None,
        #         'TP Price': None,
        #         'SL Price': None,
        #         'Result': 'Ignored',
        #         'Duration': '00:00:00',
        #         'Execution Latency': '00:00:00',
        #         'ROI': 0,
        #         'NAV': current_margin,
        #         'Ignore Reason': reason
        #     }])
        #     output_data = pd.concat([output_data, new_row], ignore_index=True)
        #     continue

        # Ignoring signals based on open trades
        exit_datetimes.sort(key=lambda x: x[0])
        ignore_signal = False
        reason = ''
        if exit_datetimes:
            later_exits = [ed for ed in exit_datetimes if ed[0] > signal_datetime]

            if len(later_exits) == 2:
                result = 'Ignored'
                reason = '2 open trades'
                ignore_signal = True
            elif len(later_exits) == 1:
                if later_exits[-1][1] != side:
                    ignore_signal = False
                else:
                    result = 'Ignored'
                    reason = 'One open trade with the same side'
                    ignore_signal = True

        if ignore_signal:
            new_row = pd.DataFrame([{
                'Datetime': signal_datetime,
                'Side': side,
                'Signal Open Price': signal_open_price,
                'Entry Price': None,
                'TP Price': None,
                'SL Price': None,
                'Result': result,
                'Duration': '00:00:00',
                'Execution Latency': '00:00:00',
                'ROI': 0,
                'NAV': current_margin,
                'Ignore Reason': reason
            }])
            output_data = pd.concat([output_data, new_row], ignore_index=True)
            continue

        # logic: ignore signals within a specific time interval before and after events
        if 'event_datetimes' in row and pd.notna(row['event_datetimes']):
            event_times = [pd.to_datetime(e.strip()) for e in str(row['event_datetimes']).split(',')]
            ignore_signal = any(event_datetime - pd.Timedelta(
                minutes=ignore_time_interval_before) <= signal_datetime <= event_datetime + pd.Timedelta(
                minutes=ignore_time_interval_after)
                                for event_datetime in event_times)
            if ignore_signal:
                new_row = pd.DataFrame([{
                    'Datetime': signal_datetime,
                    'Side': side,
                    'Signal Open Price': signal_open_price,
                    'Entry Price': None,
                    'TP Price': None,
                    'SL Price': None,
                    'Result': 'Ignored',
                    'Duration': '00:00:00',
                    'Execution Latency': '00:00:00',
                    'ROI': 0,
                    'NAV': current_margin,
                    'Ignore Reason': 'Signal around economic event'
                }])
                output_data = pd.concat([output_data, new_row], ignore_index=True)
                continue

        entry_datetime, entry_price, entry_duration = determine_entry(price_data, signal_datetime, percentage_change,
                                                                      side, time_limit_minutes, entry_time_offset)
        if entry_datetime is None:
            new_row = pd.DataFrame([{
                'Datetime': signal_datetime,
                'Side': side,
                'Signal Open Price': signal_open_price,
                'Entry Price': None,
                'TP Price': None,
                'SL Price': None,
                'Result': 'Not Filled',
                'Duration': '00:00:00',
                'Execution Latency': '00:00:00',
                'ROI': 0,
                'NAV': current_margin,
                'Ignore Reason': ''
            }])
            output_data = pd.concat([output_data, new_row], ignore_index=True)
            continue

        # Update NAV on filled order
        current_margin *= (1 - 0.0002)

        if side == 'Buy':
            tp_price = entry_price * (1 + tp)
            sl_price = entry_price * (1 - sl)
        else:
            tp_price = entry_price * (1 - tp)
            sl_price = entry_price * (1 + sl)

        result, duration_str = check_tp_sl(price_data, entry_datetime, tp_price, sl_price, side)
        exit_datetime = entry_datetime + pd.Timedelta(duration_str)

        if result in [1, -1]:
            exit_datetimes.append((exit_datetime, side))

        # Check for economic data event before the trade exit
        if 'event_datetimes' in row and pd.notna(row['event_datetimes']):
            event_times = [pd.to_datetime(e.strip()) for e in str(row['event_datetimes']).split(',')]
            for event_datetime in event_times:
                if entry_datetime < event_datetime < exit_datetime:
                    exit_datetime = event_datetime - pd.Timedelta(minutes=10)
                    if exit_datetime in price_data.index:
                        exit_price = price_data.at[exit_datetime, 'Open']
                        result = 'ended before data'
                        if (side == 'Buy' and exit_price > entry_price) or (
                                side == 'Sell' and exit_price < entry_price):
                            result += ' with profit'
                            pct_change = (exit_price - entry_price) / entry_price if side == 'Buy' else (
                                                                                                                    entry_price - exit_price) / entry_price
                            current_margin = current_margin * (1 + pct_change)
                        else:
                            result += ' with loss'
                            pct_change = (entry_price - exit_price) / entry_price if side == 'Buy' else (
                                                                                                                    exit_price - entry_price) / entry_price
                            current_margin = current_margin * (1 - pct_change)
                    else:
                        exit_price = price_data.iloc[price_data.index.get_loc(exit_datetime, method='nearest')]['Open']
                        result = 'ended before data with no exact price'
                    break

        if result not in ['ended before data with profit', 'ended before data with loss',
                          'ended before data with no exact price']:
            if result == 1:
                current_margin = current_margin * (1 + tp)
                current_margin *= (1 - 0.0005)
            elif result == -1:
                current_margin = current_margin * (1 - sl)
                current_margin *= (1 - 0.0005)

        roi = ((current_margin - initial_margin) / initial_margin) * 100
        nav = current_margin
        initial_margin = current_margin

        new_row = pd.DataFrame([{
            'Datetime': signal_datetime,
            'Side': side,
            'Signal Open Price': signal_open_price,
            'Entry Price': entry_price,
            'TP Price': tp_price,
            'SL Price': sl_price,
            'Result': result,
            'Duration': duration_str,
            'Execution Latency': format_duration(entry_duration),
            'ROI': roi,
            'NAV': nav,
            'Ignore Reason': ''
        }])

        output_data = pd.concat([output_data, new_row], ignore_index=True)

    # Ensure 'Datetime' column is in datetime format
    output_data['Datetime'] = pd.to_datetime(output_data['Datetime'])

    # Calculate Daily Return
    output_data['Date'] = output_data['Datetime'].dt.date
    daily_nav = output_data.groupby('Date')['NAV'].last().to_dict()
    daily_returns = {}
    previous_day_nav = 100000

    for date, nav in daily_nav.items():
        daily_return = (nav - previous_day_nav) / previous_day_nav * 100
        daily_returns[date] = daily_return
        previous_day_nav = nav

    output_data['Daily Return'] = output_data['Date'].map(daily_returns)
    output_data.drop(columns=['Date'], inplace=True)

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

    return result, duration_str


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


price_data = pd.read_csv('E:\Signal Backtesting\Input\price_2023-06-01_to_2024-07-30_min.csv', parse_dates=['Datetime'],
                         index_col='Datetime')
# Re-load the signal data without setting the index
signal_data = pd.read_csv('E:\Signal Backtesting\Input\sigai_signals_h.csv',
                          parse_dates=['Datetime'])



# Set a random seed for reproducibility
np.random.seed(42)
random.seed(42)

# Define the parameter ranges
tp_values = np.arange(0.009, 0.014, 0.001)
sl_values = np.arange(0.009, 0.014, 0.001)
entry_time_offset_values = np.arange(60, 120, 10)
percentage_change_values = np.arange(0.0001, 0.0015, 0.0001)
ignore_time_interval_before = np.arange(0, 1080, 60)


# Evaluation function
def evaluate(individual, interval_price_data, interval_signal_data):
    tp, sl, entry_time_offset, percentage_change = individual
    result = backtest_trades(
        interval_price_data, interval_signal_data, tp=tp, sl=sl,
        entry_time_offset=entry_time_offset,
        percentage_change=percentage_change, time_limit_minutes=120, ignore_time_interval_before=0,
        ignore_time_interval_after=0
    )

    final_nav = result['NAV'].iloc[-1]
    roi = ((final_nav - 100000) / 100000) * 100

    return roi


# Randomly initialize an individual
def create_individual():
    return [
        np.random.choice(tp_values),
        np.random.choice(sl_values),
        np.random.choice(entry_time_offset_values),
        np.random.choice(percentage_change_values)
    ]


# Mutate an individual
def mutate(individual):
    index = random.randint(0, len(individual) - 1)
    if index == 0:
        individual[index] = np.random.choice(tp_values)
    elif index == 1:
        individual[index] = np.random.choice(sl_values)
    elif index == 2:
        individual[index] = np.random.choice(entry_time_offset_values)
    elif index == 3:
        individual[index] = np.random.choice(percentage_change_values)
    return individual


# Simulated Annealing algorithm
def simulated_annealing(interval_price_data, interval_signal_data):
    current_individual = create_individual()
    current_fitness = evaluate(current_individual, interval_price_data, interval_signal_data)
    best_individual = list(current_individual)
    best_fitness = current_fitness

    initial_temperature = 1.0
    final_temperature = 0.95
    alpha = 0.99
    temperature = initial_temperature

    while temperature > final_temperature:
        new_individual = mutate(list(current_individual))
        new_fitness = evaluate(new_individual, interval_price_data, interval_signal_data)

        if new_fitness > current_fitness or random.uniform(0, 1) < np.exp(
                (new_fitness - current_fitness) / temperature):
            current_individual = new_individual
            current_fitness = new_fitness

        if current_fitness > best_fitness:
            best_individual = list(current_individual)
            best_fitness = current_fitness

        temperature *= alpha

    best_tp, best_sl, best_entry_time_offset, best_percentage_change = best_individual
    optimized_roi = best_fitness

    print(f"Optimized parameters:")
    print(f"Best Take Profit: {best_tp}")
    print(f"Best Stop Loss: {best_sl}")
    print(f"Best Entry Time Offset: {best_entry_time_offset}")
    print(f"Best Percentage Change: {best_percentage_change}")
    print(f"Optimized ROI: {optimized_roi:.4f}")

    return best_individual


def optimize_and_backtest_intervals(interval_type=None):
    if interval_type == 'weekly':
        intervals = pd.date_range(start='2024-01-01', end='2024-07-01', freq='W')
    elif interval_type == 'monthly':
        intervals = pd.date_range(start='2023-06-01', end='2024-08-01', freq='MS')

    import logging
    import pandas as pd

    # Configure logging
    logging.basicConfig(level=logging.DEBUG, format='%(asctime)s - %(levelname)s - %(message)s')

    def optimize_and_backtest_intervals(interval_type=None):
        if interval_type == 'weekly':
            intervals = pd.date_range(start='2024-01-01', end='2024-07-01', freq='W')
        elif interval_type == 'monthly':
            intervals = pd.date_range(start='2023-06-01', end='2024-08-01', freq='MS')

        logging.debug("Intervals Generated: %s", intervals)
        reports = []

        for i in range(1, len(intervals) - 1):
            logging.debug("Processing Interval %d/%d", i, len(intervals) - 2)
            start_date = intervals[i - 1]
            end_date = intervals[i]
            interval_price_data = price_data[start_date:end_date]
            interval_signal_data = signal_data[
                (signal_data['Datetime'] >= start_date) & (signal_data['Datetime'] < end_date)]
            logging.debug("Start Date: %s, End Date: %s", start_date, end_date)

            best_individual = simulated_annealing(interval_price_data, interval_signal_data)
            logging.debug("Best Individual for Interval %d: %s", i, best_individual)

            tp, sl, entry_time_offset, percentage_change = best_individual

            next_start_date = intervals[i]
            next_end_date = intervals[i + 1]

            if next_end_date not in price_data.index:
                next_end_date = price_data.index[price_data.index.get_loc(next_end_date, method='pad')]

            next_interval_price_data = price_data[next_start_date:next_end_date]
            next_interval_signal_data = signal_data[
                (signal_data['Datetime'] >= next_start_date) & (signal_data['Datetime'] < next_end_date)]
            logging.debug("Next Start Date: %s, Next End Date: %s", next_start_date, next_end_date)

            # Ensure entry_time_offset is within the bounds of next_interval_price_data
            entry_time_offset = min(entry_time_offset, len(next_interval_price_data) - 1)

            # Calculate the maximum time_limit_minutes to stay within the end of the month
            max_time_limit = (next_interval_price_data.index[-1] - next_interval_price_data.index[
                0]).total_seconds() / 60
            time_limit_minutes = min(120, max_time_limit)

            result = backtest_trades(
                next_interval_price_data, next_interval_signal_data, tp=tp, sl=sl,
                entry_time_offset=entry_time_offset,
                percentage_change=percentage_change, time_limit_minutes=time_limit_minutes,
                ignore_time_interval_before=0,
                ignore_time_interval_after=0
            )
            logging.debug("Backtest Result: %s", result)

            total_trades = len(result[result['Result'].isin([-1, 1])])
            wins = len(result[result['Result'] == 1])
            losses = len(result[result['Result'] == -1])
            win_percentage = wins / total_trades
            final_nav = result['NAV'].iloc[-1]
            daily_nav = result.set_index('Datetime')['Daily Return']
            logging.debug("Total Trades: %d, Wins: %d, Losses: %d, Win Percentage: %f", total_trades, wins, losses,
                          win_percentage)
            logging.debug("Final NAV: %f", final_nav)

            for nav_date, daily_nav in daily_nav.items():
                report = {
                    'Date': nav_date,
                    'Start Date': next_start_date,
                    'End Date': next_end_date,
                    'Total Trades': total_trades,
                    'Wins': wins,
                    'Losses': losses,
                    'WinRate': win_percentage,
                    'Final NAV': final_nav,
                    'Daily Return': daily_nav,
                    'TP': tp,
                    'SL': sl,
                    'Entry Time Offset': entry_time_offset,
                    'Percentage Change': percentage_change
                }
                reports.append(report)

        report_df = pd.DataFrame(reports)
        report_df['Date'] = pd.to_datetime(report_df['Date'])
        report_df.set_index('Date', inplace=True)
        logging.debug("Final Report DataFrame: %s", report_df)
        return report_df


report_df = optimize_and_backtest_intervals(interval_type='monthly')
report_df.to_csv('E:\Signal Backtesting\Output\Monthly_optimization_with_complete_signals.csv')