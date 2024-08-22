import pandas as pd
import numpy as np
from deap import base, creator, tools, algorithms
import talib as ta
import random

class TradingStrategyBaseline:
    def __init__(self, price_data, signal_data):
        self.price_data = price_data
        self.signal_data = signal_data

    # Measure functions
    def _calculate_atr(self, window):
        return ta.ATR(self.price_data['High'], self.price_data['Low'], self.price_data['Close'], timeperiod=window)

    def _calculate_trend(self, window):
        return ta.EMA(self.price_data['Close'], timeperiod=window)

    def _calculate_momentum(self, window):
        return ta.RSI(self.price_data['Close'], timeperiod=window)

    def _calculate_measures(self):
        intervals = {'5m': 5, '15m': 15, '1h': 60, '4h': 240, '1D': 1440, '1W': 10080}
        measures = {}

        for key, minutes in intervals.items():
            measures[f'ATR_{key}'] = self._calculate_atr(minutes)
            measures[f'Trend_{key}'] = self._calculate_trend(minutes)
            measures[f'Momentum_{key}'] = self._calculate_momentum(minutes)

        return measures

    def _ensure_sum_of_weights(self, weights):
        total_weight = sum(weights)
        return [w / total_weight for w in weights]

    def _calculate_parameters(self, measures, individual):
        # Normalize the weights
        atr_weights = self._ensure_sum_of_weights(individual[:6])
        trend_weights = self._ensure_sum_of_weights(individual[6:12])
        momentum_weights = self._ensure_sum_of_weights(individual[12:18])

        # Calculate the raw values by summing weighted measures
        raw_tp = sum(atr_weights[i] * measures[f'ATR_{key}'].iloc[-1] +
                     trend_weights[i] * measures[f'Trend_{key}'].iloc[-1] +
                     momentum_weights[i] * measures[f'Momentum_{key}'].iloc[-1]
                     for i, key in enumerate(['5m', '15m', '1h', '4h', '1D', '1W']))

        raw_sl = sum(atr_weights[i] * measures[f'ATR_{key}'].iloc[-1] +
                     trend_weights[i] * measures[f'Trend_{key}'].iloc[-1] +
                     momentum_weights[i] * measures[f'Momentum_{key}'].iloc[-1]
                     for i, key in enumerate(['5m', '15m', '1h', '4h', '1D', '1W']))

        raw_percentage_change = sum(atr_weights[i] * measures[f'ATR_{key}'].iloc[-1] +
                                    trend_weights[i] * measures[f'Trend_{key}'].iloc[-1] +
                                    momentum_weights[i] * measures[f'Momentum_{key}'].iloc[-1]
                                    for i, key in enumerate(['5m', '15m', '1h', '4h', '1D', '1W']))

        raw_entry_time_offset = sum(atr_weights[i] * measures[f'ATR_{key}'].iloc[-1] +
                                    trend_weights[i] * measures[f'Trend_{key}'].iloc[-1] +
                                    momentum_weights[i] * measures[f'Momentum_{key}'].iloc[-1]
                                    for i, key in enumerate(['5m', '15m', '1h', '4h', '1D', '1W']))

        # Determine the min/max range for raw values across all possible measures (example values)
        raw_min_tp, raw_max_tp = 0, np.max(
            [measures[f'ATR_{key}'].max() for key in ['5m', '15m', '1h', '4h', '1D', '1W']])
        raw_min_sl, raw_max_sl = 0, np.max(
            [measures[f'ATR_{key}'].max() for key in ['5m', '15m', '1h', '4h', '1D', '1W']])
        raw_min_percentage_change, raw_max_percentage_change = 0, np.max(
            [measures[f'ATR_{key}'].max() for key in ['5m', '15m', '1h', '4h', '1D', '1W']])
        raw_min_entry_time_offset, raw_max_entry_time_offset = 0, np.max(
            [measures[f'ATR_{key}'].max() for key in ['5m', '15m', '1h', '4h', '1D', '1W']])

        # Normalize the raw values to the range [0, 1]
        def normalize(value, min_val, max_val):
            return (value - min_val) / (max_val - min_val) if max_val != min_val else 0

        norm_tp = normalize(raw_tp, raw_min_tp, raw_max_tp)
        norm_sl = normalize(raw_sl, raw_min_sl, raw_max_sl)
        norm_percentage_change = normalize(raw_percentage_change, raw_min_percentage_change, raw_max_percentage_change)
        norm_entry_time_offset = normalize(raw_entry_time_offset, raw_min_entry_time_offset, raw_max_entry_time_offset)

        # Map the normalized values to the desired parameter ranges
        tp = 0.004 + norm_tp * (0.014 - 0.004)
        sl = 0.004 + norm_sl * (0.014 - 0.004)
        if tp <= sl:
            tp = sl + 0.001

        percentage_change = 0.0001 + norm_percentage_change * (0.0015 - 0.0001)
        entry_time_offset = int(norm_entry_time_offset * 120)

        return tp, sl, percentage_change, entry_time_offset

    # Helper function to format the duration
    def _format_duration(self, duration):
        seconds = duration.total_seconds()
        hours = int(seconds // 3600)
        minutes = int((seconds % 3600) // 60)
        seconds = int(seconds % 60)
        return f"{hours:02}:{minutes:02}:{seconds:02}"

    # Helper function to check if TP or SL is hit
    def _check_tp_sl(self, entry_datetime, tp_price, sl_price, side):
        result = 0
        exit_datetime = None
        subsequent_prices = self.price_data.loc[entry_datetime:]

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
            duration_str = self._format_duration(duration)
        else:
            duration_str = '00:00:00'

        return result, duration_str

    # Helper function to determine the entry point
    def _determine_entry(self, signal_datetime, percentage_change, side, open_order_elimination, entry_time_offset):
        adjusted_signal_datetime = signal_datetime + pd.Timedelta(minutes=entry_time_offset)
        if adjusted_signal_datetime not in self.price_data.index:
            return None, None, None

        adjusted_open_price = self.price_data.at[adjusted_signal_datetime, 'Open']
        percentage_change_price = adjusted_open_price * (
                1 - percentage_change) if side == 'Buy' else adjusted_open_price * (1 + percentage_change)

        time_limit = adjusted_signal_datetime + pd.Timedelta(minutes=open_order_elimination)
        subsequent_prices = self.price_data.loc[adjusted_signal_datetime:time_limit]

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

    # Example backtest function
    def backtest_trades(self, price_data, signal_data, tp=None, sl=None, entry_time_offset=None,
                        percentage_change=None, open_order_elimination=None, ignore_time_interval_before=None,
                        ignore_time_interval_after=None):
        output_data = pd.DataFrame(columns=[
            'Datetime', 'Side', 'Signal Open Price', 'Entry Price', 'TP Price', 'SL Price', 'Result', 'Duration',
            'Execution Latency', 'ROI', 'NAV', 'Ignore Reason'
        ])

        initial_margin = 100000
        current_margin = initial_margin
        exit_datetimes = []
        initial_drawdown = 0  # For initial drawdown calculation
        nav_history = []

        for i, row in signal_data.iterrows():
            signal_datetime = row['Datetime']
            signal_value = row['Signal']

            if signal_value == 0:
                continue
            elif signal_value > 0:
                side = 'Buy'
            else:
                side = 'Sell'

            adjusted_signal_datetime = signal_datetime + pd.Timedelta(minutes=entry_time_offset)
            signal_open_price = price_data.at[adjusted_signal_datetime, 'Open']

            # Ignoring signals based on open trades
            exit_datetimes.sort(key=lambda x: x[0])
            ignore_signal = False
            reason = ''

            if exit_datetimes:
                later_exits = [ed for ed in exit_datetimes if ed[0] > signal_datetime]

                if len(later_exits) >= 2:
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

            entry_datetime, entry_price, entry_duration = self._determine_entry(signal_datetime, percentage_change,
                                                                                side, open_order_elimination,
                                                                                entry_time_offset)
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

            result, duration_str = self._check_tp_sl(entry_datetime, tp_price, sl_price, side)
            exit_datetime = entry_datetime + pd.Timedelta(duration_str)

            if result in [1, -1]:
                exit_datetimes.append((exit_datetime, side))

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
                        break

            if result not in ['ended before data with profit', 'ended before data with loss',
                              'ended before data with no exact price']:

                if result == 1:
                    current_margin = current_margin * (1 + tp)
                    current_margin *= (1 - 0.0005)
                elif result == -1:
                    current_margin = current_margin * (1 - sl)
                    current_margin *= (1 - 0.0005)

            # Calculate initial drawdown
            if current_margin < 100000:
                drawdown = ((100000 - current_margin) / 100000) * 100
                initial_drawdown = max(initial_drawdown, drawdown)

            roi = ((current_margin - initial_margin) / initial_margin) * 100
            nav = current_margin
            initial_margin = current_margin

            nav_history.append(nav)
            new_row = pd.DataFrame([{
                'Datetime': signal_datetime,
                'Side': side,
                'Signal Open Price': signal_open_price,
                'Entry Price': entry_price,
                'TP Price': tp_price,
                'SL Price': sl_price,
                'Result': result,
                'Duration': duration_str,
                'Execution Latency': self._format_duration(entry_duration),
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
            daily_return = ((nav - previous_day_nav) / previous_day_nav) * 100
            daily_returns[date] = daily_return
            previous_day_nav = nav

        output_data['Daily Return'] = output_data['Date'].map(daily_returns)
        output_data.drop(columns=['Date'], inplace=True)

        # Calculate Monthly Max Drawdown
        output_data['Month'] = output_data['Datetime'].dt.to_period('M')
        monthly_max_drawdowns = {}

        for month, group in output_data.groupby('Month'):
            peak_nav = group['NAV'].iloc[0]  # Start with the first NAV of the month
            max_drawdown_in_month = 0
            local_peak = peak_nav

            for nav in group['NAV']:
                if nav > local_peak:
                    local_peak = nav  # Update the peak if a new high is found
                else:
                    # Calculate drawdown from the peak to the current NAV
                    drawdown = ((local_peak - nav) / local_peak) * 100
                    max_drawdown_in_month = max(max_drawdown_in_month, drawdown)  # Track the maximum drawdown

            monthly_max_drawdowns[month] = max_drawdown_in_month

        output_data['Monthly Max Drawdown'] = output_data['Month'].map(monthly_max_drawdowns)
        output_data['Initial Drawdown'] = initial_drawdown

        output_data.drop(columns=['Month'], inplace=True)

        return output_data

    # Evaluation function
    def evaluate(self, individual):
        measures = self._calculate_measures()
        # Normalize the weights
        atr_weights = self._ensure_sum_of_weights(individual[:6])
        trend_weights = self._ensure_sum_of_weights(individual[6:12])
        momentum_weights = self._ensure_sum_of_weights(individual[12:18])

        # Print the multipliers (weights)
        print("ATR Weights:", atr_weights)
        print("Trend Weights:", trend_weights)
        print("Momentum Weights:", momentum_weights)
        # Calculate tp, sl, percentage_change, and entry_time_offset using the measures and weights
        tp, sl, percentage_change, entry_time_offset = self._calculate_parameters(measures, individual)
        print(
            f"Evaluating with tp: {tp}, sl: {sl}, percentage_change: {percentage_change}, entry_time_offset: {entry_time_offset}")
        monthly_rois = []
        penalty_weight = 1

        # Iterate over each month from June 2023 to July 2024 and evaluate the strategy
        for year, month_range in [(2024, range(1, 8))]:
            for month in month_range:
                month_signal_data = self.signal_data[
                    (self.signal_data['Datetime'].dt.year == year) &
                    (self.signal_data['Datetime'].dt.month == month)
                ]

                result = self.backtest_trades(
                    self.price_data,
                    month_signal_data,
                    tp=tp,
                    sl=sl,
                    entry_time_offset=entry_time_offset,
                    percentage_change=percentage_change,
                    open_order_elimination=120,
                    ignore_time_interval_before=1020,
                    ignore_time_interval_after=0
                )

                final_nav = 100000
                if not result.empty and 'NAV' in result.columns:
                    final_nav = result['NAV'].iloc[-1]

                roi = ((final_nav - 100000) / 100000) * 100
                monthly_rois.append(roi)
                print(f"  Individual {individual}: {year}-{month} ROI: {roi:.2f}%")

        sum_roi = sum(monthly_rois)
        penalty = sum(penalty_weight * roi for roi in monthly_rois if roi < 0)

        fitness = sum_roi + penalty
        print(f"Sum of ROIs: {sum_roi:.2f}, Penalty: {penalty:.2f}, Fitness: {fitness:.4f}")

        return fitness,

    # Genetic Algorithm optimization
    def optimize_parameters(self):
        # Define FitnessMax correctly
        creator.create("FitnessMax", base.Fitness, weights=(1.0,))
        creator.create("Individual", list, fitness=creator.FitnessMax)

        toolbox = base.Toolbox()

        # Attribute generator: Define random weights between 0 and 1
        toolbox.register("attr_float", np.random.uniform, 0, 1)

        # Structure initializers: Define individuals and population
        toolbox.register("individual", tools.initRepeat, creator.Individual, toolbox.attr_float,
                         n=18)  # 6 intervals * 3 measures
        toolbox.register("population", tools.initRepeat, list, toolbox.individual)

        # Register evaluation function
        toolbox.register("evaluate", self.evaluate)

        # Register genetic operators with adaptive parameters
        def adaptive_crossover(ind1, ind2):
            alpha = random.uniform(0.5, 1.0)
            return tools.cxBlend(ind1, ind2, alpha=alpha)

        def adaptive_mutation(individual):
            sigma = random.uniform(0.05, 0.2)
            indpb = random.uniform(0.05, 0.2)
            return tools.mutGaussian(individual, mu=0, sigma=sigma, indpb=indpb)

        def adaptive_selection(population, k):
            tournsize = random.randint(3, 5)
            return tools.selTournament(population, k, tournsize=tournsize)

        toolbox.register("mate", adaptive_crossover)
        toolbox.register("mutate", adaptive_mutation)
        toolbox.register("select", adaptive_selection)

        # Initialize population
        population = toolbox.population(n=100)

        # Genetic Algorithm flow
        algorithms.eaSimple(population, toolbox, cxpb=0.5, mutpb=0.2, ngen=40, verbose=True)

        # Get the best individual
        best_individual = tools.selBest(population, k=1)[0]
        fitness = self.evaluate(best_individual)

        tp, sl, percentage_change, entry_time_offset = self._calculate_parameters(self._calculate_measures(), best_individual)

        return best_individual, tp, sl, percentage_change, entry_time_offset

# Example usage
price_data = pd.read_csv('E:\Signal Backtesting\Input\price_2023-06-01_to_2024-08-4_min.csv', parse_dates=['Datetime'], index_col='Datetime')
signal_data = pd.read_csv('E:\Signal Backtesting\Input\\filtered_signals_with_2023_2024_all_events_plus_one_hour.csv', parse_dates=['Datetime'])

# Instantiate the strategy and optimize parameters
strategy_baseline = TradingStrategyBaseline(price_data, signal_data)
optimal_params, tp, sl, percentage_change, entry_time_offset = strategy_baseline.optimize_parameters()
print("Optimal Parameters:", optimal_params)
print(f"Calculated TP: {tp}")
print(f"Calculated SL: {sl}")
print(f"Calculated Percentage Change: {percentage_change}")
print(f"Calculated Entry Time Offset: {entry_time_offset}")
