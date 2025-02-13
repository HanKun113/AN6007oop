from flask import Flask, request, jsonify, render_template
import json
import random
import os
import datetime
import shutil
import calendar
import pandas as pd
from dataclasses import dataclass
from typing import Dict, List, Optional

@dataclass
class MeterReading:
    meter_id: str
    reading_time: str
    meter_value: float

class SmartMeterSystem:
    def __init__(self, base_dir: str):
        self.base_dir = base_dir
        self.data_dir = os.path.join(base_dir, 'data')
        self.accounts_file = os.path.join(self.data_dir, "all_account.json")
        self.current_time_file = os.path.join(self.data_dir, "current_time.json")
        self.daily_readings_dir = os.path.join(self.data_dir, "daily_readings")
        self.monthly_readings_dir = os.path.join(self.data_dir, "month_readings")
        
        self.latest_readings: Dict[str, float] = {}
        self.daily_cache: List[MeterReading] = []
        
        self._ensure_directories()
        
    def _ensure_directories(self):
        """Ensure all required directories exist."""
        os.makedirs(self.data_dir, exist_ok=True)
        os.makedirs(self.daily_readings_dir, exist_ok=True)
        os.makedirs(self.monthly_readings_dir, exist_ok=True)
    
    def get_month_directory(self, base_dir: str, date: datetime.datetime) -> str:
        """Get the directory for the specified month."""
        month_dir = os.path.join(base_dir, date.strftime("%Y%m"))
        os.makedirs(month_dir, exist_ok=True)
        return month_dir
    
    def get_current_time(self) -> datetime.datetime:
        """Get the current simulation time."""
        if os.path.exists(self.current_time_file):
            with open(self.current_time_file, "r") as f:
                return datetime.datetime.fromisoformat(json.load(f)["current_time"])
        else:
            initial_time = datetime.datetime(2024, 5, 1)
            self.save_current_time(initial_time)
            return initial_time
    
    def save_current_time(self, current_time: datetime.datetime):
        """Save the current simulation time."""
        with open(self.current_time_file, "w") as f:
            json.dump({"current_time": current_time.isoformat()}, f)
    
    def load_accounts(self) -> List[dict]:
        """Load all registered accounts."""
        if os.path.exists(self.accounts_file):
            with open(self.accounts_file, "r", encoding="utf-8") as f:
                try:
                    accounts = json.load(f)
                    return accounts if isinstance(accounts, list) else []
                except json.JSONDecodeError:
                    return []
        return []
    
    def save_accounts(self, accounts: List[dict]):
        """Save account information."""
        os.makedirs(os.path.dirname(self.accounts_file), exist_ok=True)
        with open(self.accounts_file, "w", encoding="utf-8") as f:
            json.dump(accounts, f, ensure_ascii=False, indent=2)
    
    def register_meter(self, meter_id: str, area: str, dwelling: str) -> dict:
        """Register a new meter."""
        accounts = self.load_accounts()
        
        if any(account["meter_ID"] == meter_id for account in accounts):
            raise ValueError("Meter ID already exists")
            
        current_time = self.get_current_time()
        formatted_time = current_time.strftime("%Y-%m-%dT%H:%M:%S")
        
        account = {
            "meter_ID": meter_id,
            "area": area,
            "dwelling": dwelling,
            "register_time": formatted_time
        }
        
        reading = MeterReading(
            meter_id=meter_id,
            reading_time=formatted_time,
            meter_value=0
        )
        
        accounts.append(account)
        self.save_accounts(accounts)
        
        self.latest_readings[meter_id] = 0
        self.daily_cache.append(reading)
        
        return account
    
    def collect_readings(self, increment_unit: str = 'days', increment_value: int = 1) -> dict:
        """Collect meter readings for the specified time period."""
        accounts = self.load_accounts()
        if not accounts:
            raise ValueError("No registered accounts")
            
        current_time = self.get_current_time()
        next_time = self._calculate_next_time(current_time, increment_unit, increment_value)
        
        all_readings = []
        temp_current = current_time
        
        # If increment unit is 'months', generate readings day-by-day.
        if increment_unit == 'months':
            while temp_current < next_time:
                daily_readings = self._generate_readings(
                    temp_current,
                    min(temp_current + datetime.timedelta(days=1), next_time),
                    accounts
                )
                all_readings.extend(daily_readings)
                temp_current += datetime.timedelta(days=1)
        else:
            # For other cases (days, hours, minutes), use the original logic.
            all_readings = self._generate_readings(current_time, next_time, accounts)
        
        self.save_current_time(next_time)
        
        return {
            "message": f"Readings collected from {current_time} to {next_time}",
            "readings_count": len(all_readings),
            "sample_readings": all_readings[:3] if all_readings else [],
            "new_time": next_time.isoformat()
        }
    
    def _calculate_next_time(
        self, 
        current_time: datetime.datetime,
        increment_unit: str,
        increment_value: int
    ) -> datetime.datetime:
        """Calculate the next time based on the increment."""
        if increment_unit == 'minutes':
            return current_time + datetime.timedelta(minutes=increment_value)
        elif increment_unit == 'hours':
            return current_time + datetime.timedelta(hours=increment_value)
        elif increment_unit == 'days':
            return current_time + datetime.timedelta(days=increment_value)
        elif increment_unit == 'months':
            # Calculate the same day in the next month.
            next_month = current_time.month + increment_value
            next_year = current_time.year + (next_month - 1) // 12
            next_month = ((next_month - 1) % 12) + 1
            
            # Handle end-of-month issues (e.g., March 31 + 1 month should be April 30).
            last_day_of_next_month = calendar.monthrange(next_year, next_month)[1]
            next_day = min(current_time.day, last_day_of_next_month)
            
            return current_time.replace(
                year=next_year, 
                month=next_month, 
                day=next_day,
                hour=current_time.hour,
                minute=current_time.minute
            )
        else:
            raise ValueError("Invalid time unit")
    
    def _generate_readings(self, current_time: datetime.datetime, next_time: datetime.datetime, accounts: List[dict]) -> List[dict]:
        all_readings = []
        current = current_time
        
        # ======= Added: If the target time span exceeds one day, generate data day-by-day =======
        if (next_time - current_time).total_seconds() > 86400:
            temp_current = current_time
            while temp_current < next_time:
                # Calculate the end of the day (not exceeding next_time)
                day_end = min(temp_current + datetime.timedelta(days=1), next_time)
                # Recursively call _generate_readings to process data within one day (where the time span is <= 1 day)
                daily_readings = self._generate_readings(temp_current, day_end, accounts)
                all_readings.extend(daily_readings)
                temp_current += datetime.timedelta(days=1)
            return all_readings
        # ======= End of added section =======
        
        # Do not skip immediately at midnight; stay at midnight.
        current = current.replace(minute=0, second=0, microsecond=0)
        
        while current <= next_time:
            # If in the maintenance period (0:00-1:00)
            if current.hour == 0:
                process_date = current - datetime.timedelta(minutes=1)
                # Assume the simulation start date is 2024-05-01
                if process_date.date() >= datetime.date(2024, 5, 1):
                    self._process_daily_data(process_date)
                if current.day == 1:
                    self._archive_and_prepare_monthly_data(current)
                current = current.replace(hour=1)
                continue
                
            # Generate the next reading time point (every 30 minutes)
            reading_time = current + datetime.timedelta(minutes=30)
            
            # If the next reading time exceeds the end time or enters the maintenance period, break the loop.
            if reading_time > next_time or reading_time.hour == 0:
                break
                
            # Generate a reading for each account.
            for account in accounts:
                meter_id = account["meter_ID"]
                previous_value = self.latest_readings.get(meter_id, 0)
                increment = random.uniform(0, 1)
                meter_value = previous_value + increment
                self.latest_readings[meter_id] = meter_value
                
                reading_dict = {
                    "meter_ID": meter_id,
                    "reading_time": reading_time.isoformat(),
                    "meter_value": round(meter_value, 3)
                }
                all_readings.append(reading_dict)
                
                # Convert to a MeterReading object and add to daily_cache.
                reading = MeterReading(
                    meter_id=meter_id,
                    reading_time=reading_time.isoformat(),
                    meter_value=round(meter_value, 3)
                )
                self.daily_cache.append(reading)
            
            current = reading_time
        
        # Process data for the last day
        if self.daily_cache:
            # Use the time of the last reading in daily_cache as the archiving date.
            last_reading_time = datetime.datetime.fromisoformat(self.daily_cache[-1].reading_time)
            self._process_daily_data(last_reading_time)
                
        return all_readings
    
    def _process_daily_data(self, current_date: datetime.datetime):
        """Process and save daily data."""
        if not self.daily_cache:
            return
        
        basic_data = [{
            "date": current_date.strftime("%Y-%m-%d"),
            "time": datetime.datetime.fromisoformat(reading.reading_time).strftime("%H:%M"),
            "meter_ID": reading.meter_id,
            "meter_value": reading.meter_value
        } for reading in self.daily_cache]
        
        if basic_data:
            df_basic = pd.DataFrame(basic_data)
            daily_file = self._get_daily_file_path(current_date)
            os.makedirs(os.path.dirname(daily_file), exist_ok=True)
            
            if current_date.day == 1:
                df_basic.to_csv(daily_file, index=False)
            else:
                if os.path.exists(daily_file):
                    df_basic.to_csv(daily_file, mode='a', header=False, index=False)
                else:
                    df_basic.to_csv(daily_file, index=False)
    
        self.daily_cache.clear()
    
    def _get_daily_file_path(self, date: datetime.datetime) -> str:
        """Get the file path for daily readings."""
        month_dir = self.get_month_directory(self.daily_readings_dir, date)
        return os.path.join(month_dir, f"readings_{date.strftime('%Y%m%d')}.csv")
    
    def _archive_and_prepare_monthly_data(self, current_date: datetime.datetime):
        """Process historical data and prepare for a new month during maintenance."""
        # Get the first day of the current month.
        first_of_current = current_date.replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        last_month = first_of_current - datetime.timedelta(days=1)
        last_month_first = last_month.replace(day=1)
        
        # Check the months available in daily_readings_dir.
        available_months = []
        if os.path.exists(self.daily_readings_dir):
            for year_month_dir in os.listdir(self.daily_readings_dir):
                try:
                    year = int(year_month_dir[:4])
                    month = int(year_month_dir[4:])
                    if datetime.datetime(year, month, 1) >= datetime.datetime(2024, 5, 1):  # Process only data after May 2024
                        available_months.append(datetime.datetime(year, month, 1))
                except ValueError:
                    continue
        
        # If there are fewer than 2 available months, do not archive.
        if len(available_months) < 2:
            return
            
        # Get the month to process (n-2 month)
        month_to_process = last_month_first - datetime.timedelta(days=1)
        month_to_process = month_to_process.replace(day=1)
        
        # Check if the month to process is within the valid range.
        if month_to_process < datetime.datetime(2024, 5, 1):
            return
            
        # Get the related directories.
        process_month_daily_dir = self.get_month_directory(self.daily_readings_dir, month_to_process)
        process_month_monthly_dir = self.get_month_directory(self.monthly_readings_dir, month_to_process)
        
        if os.path.exists(process_month_daily_dir):
            # Read all daily readings files.
            daily_files = [f for f in os.listdir(process_month_daily_dir) if f.startswith('readings_')]
            all_readings = []
            accounts = {str(acc["meter_ID"]): acc for acc in self.load_accounts()}
            
            for file in daily_files:
                file_path = os.path.join(process_month_daily_dir, file)
                df = pd.read_csv(file_path)
                df['meter_ID'] = df['meter_ID'].astype(str)
                df['date_time'] = pd.to_datetime(df['date'] + ' ' + df['time'])
                all_readings.append(df)
            
            if all_readings:
                df_combined = pd.concat(all_readings, ignore_index=True)
                df_combined['meter_value'] = df_combined['meter_value'].astype(float)
                
                # Process monthly consumption data.
                self._process_monthly_consumption(df_combined, accounts, month_to_process, process_month_monthly_dir)
                
                # Process area analysis data.
                self._process_area_analysis(
                    df_combined,
                    accounts,
                    month_to_process,
                    month_to_process.replace(day=calendar.monthrange(month_to_process.year, month_to_process.month)[1]),
                    process_month_monthly_dir
                )
                
                # Process area monthly summary data.
                self._process_area_monthly_summary(df_combined, accounts, month_to_process, process_month_monthly_dir)
        
        # Clean up old daily readings (keep only the last two months of data).
        self._cleanup_old_readings(last_month_first)
    
    def _process_monthly_consumption(self, df_combined: pd.DataFrame, accounts: Dict, 
                                     last_month: datetime.datetime, last_month_monthly_dir: str):
        """Process monthly consumption data."""
        first_readings = df_combined.sort_values('date_time').groupby('meter_ID').first()
        last_readings = df_combined.sort_values('date_time').groupby('meter_ID').last()
        
        monthly_consumption_data = []
        for meter_id in first_readings.index:
            if meter_id in accounts:
                consumption = last_readings.loc[meter_id, 'meter_value'] - first_readings.loc[meter_id, 'meter_value']
                monthly_consumption_data.append({
                    'meter_ID': meter_id,
                    'month_consumption': round(consumption, 3)
                })
        
        if monthly_consumption_data:
            month_summary_file = os.path.join(
                last_month_monthly_dir,
                f"monthly_summary_{last_month.strftime('%Y%m')}.csv"
            )
            df_summary = pd.DataFrame(monthly_consumption_data)
            df_summary.to_csv(month_summary_file, sep=';', index=False)
    
    def _process_area_monthly_summary(self, df_combined: pd.DataFrame, accounts: Dict,
                                      process_month: datetime.datetime, monthly_dir: str):
        """Process area monthly consumption summary."""
        area_monthly_summary = []
        
        # Get all unique areas.
        unique_areas = set(acc['area'] for acc in accounts.values())
        
        for area in unique_areas:
            # Get all meters for the area.
            area_meters = [meter_id for meter_id, acc in accounts.items() if acc['area'] == area]
            
            # Filter data for this area.
            area_data = df_combined[df_combined['meter_ID'].isin(area_meters)]
            
            if not area_data.empty:
                # Calculate total consumption for the area (sum of each meter's consumption).
                area_total = 0
                for meter_id in area_meters:
                    meter_data = area_data[area_data['meter_ID'] == meter_id].sort_values('date_time')
                    if not meter_data.empty:
                        meter_consumption = meter_data['meter_value'].iloc[-1] - meter_data['meter_value'].iloc[0]
                        area_total += meter_consumption
                
                area_monthly_summary.append({
                    'area': area,
                    'month': process_month.strftime('%Y-%m'),
                    'total_consumption': round(area_total, 3),
                    'meter_count': len(area_meters)
                })
        
        if area_monthly_summary:
            summary_file = os.path.join(
                monthly_dir,
                f"area_monthly_summary_{process_month.strftime('%Y%m')}.csv"
            )
            df_summary = pd.DataFrame(area_monthly_summary)
            df_summary.to_csv(summary_file, sep=';', index=False)
    
    def _process_area_analysis(self, df_combined: pd.DataFrame, accounts: Dict,
                               last_month_first: datetime.datetime, last_month: datetime.datetime,
                               last_month_monthly_dir: str):
        """Process area analysis data."""
        daily_area_analysis = []
        
        for date in pd.date_range(start=last_month_first, end=last_month):
            date_str = date.strftime('%Y-%m-%d')
            daily_data = df_combined[df_combined['date'] == date_str]
            
            if not daily_data.empty:
                for meter_id in accounts:
                    meter_data = daily_data[daily_data['meter_ID'] == meter_id]
                    if not meter_data.empty:
                        consumption = meter_data['meter_value'].max() - meter_data['meter_value'].min()
                        daily_area_analysis.append({
                            'DateID': date_str,
                            'AreaID': accounts[meter_id]['area'],
                            'dwelling_type_id': accounts[meter_id]['dwelling'],
                            'kwh_per_acc': round(consumption, 3)
                        })
        
        if daily_area_analysis:
            area_analysis_file = os.path.join(
                last_month_monthly_dir,
                f"area_analysis_{last_month.strftime('%Y%m')}.csv"
            )
            df_area = pd.DataFrame(daily_area_analysis)
            df_area.to_csv(area_analysis_file, sep=';', index=False)
    
    def _cleanup_old_readings(self, last_month_first: datetime.datetime):
        """Clean up old daily readings."""
        if os.path.exists(self.daily_readings_dir):
            for year_month_dir in os.listdir(self.daily_readings_dir):
                try:
                    year = int(year_month_dir[:4])
                    month = int(year_month_dir[4:])
                    dir_date = datetime.datetime(year, month, 1)
                    
                    # Only process data after May 2024.
                    if dir_date < datetime.datetime(2024, 5, 1):
                        continue
                        
                    if dir_date < last_month_first:
                        dir_path = os.path.join(self.daily_readings_dir, year_month_dir)
                        shutil.rmtree(dir_path)
                except ValueError:
                    continue
    
    def reset_system(self):
        """Reset the entire system to its initial state."""
        try:
            # Clear directories
            for directory in [self.daily_readings_dir, self.monthly_readings_dir]:
                if os.path.exists(directory):
                    shutil.rmtree(directory)
                os.makedirs(directory)
            
            # Reset the accounts file
            with open(self.accounts_file, 'w', encoding='utf-8') as f:
                json.dump([], f, ensure_ascii=False, indent=2)
            
            # Reset the time file
            self.save_current_time(datetime.datetime(2024, 5, 1))
            
            # Clear caches
            self.latest_readings.clear()
            self.daily_cache.clear()
            
            return True
        except Exception as e:
            print(f"Reset failed: {str(e)}")
            return False

# Flask application setup
app = Flask(__name__, 
    template_folder='templates',  # Specify the templates directory
    static_folder='static'         # Specify the static files directory
)
meter_system = SmartMeterSystem(os.path.dirname(os.path.abspath(__file__)))

@app.route("/")
def index():
    """Render the index page."""
    return render_template("index.html")

@app.route('/collect')
def collect():
    """Render the collection page."""
    return render_template('collect.html')

@app.route("/register", methods=["GET", "POST"])
def register():
    # GET request: Display the registration page.
    if request.method == "GET":
        return render_template("register.html")
        
    # POST request: Process the registration logic.
    try:
        data = request.get_json()
        account = meter_system.register_meter(
            data["meterId"],
            data["area"],
            data["dwelling"]
        )
        return jsonify({"success": True, "account": account})
    except Exception as e:
        return jsonify({"success": False, "message": str(e)}), 400

@app.route("/current_time", methods=["GET"])
def get_current_time():
    current_time = meter_system.get_current_time()
    return jsonify({
        "Current Simulation Time": {
            "Date": current_time.strftime("%Y-%m-%d"),
            "Time": current_time.strftime("%H:%M:%S"),
            "Weekday": current_time.strftime("%A")
        }
    })

@app.route("/meter_reading", methods=["POST"])
def meter_reading():
    try:
        data = request.get_json()
        if not data:
            return jsonify({"error": "No data provided"}), 400
            
        unit = data.get('unit', 'days')
        try:
            value = int(data.get('value', 1))
        except (TypeError, ValueError):
            return jsonify({"error": "Invalid value format"}), 400
            
        result = meter_system.collect_readings(unit, value)
        return jsonify(result), 200
        
    except ValueError as e:
        return jsonify({"error": str(e)}), 400
    except Exception as e:
        import traceback
        print("Error in meter_reading:", str(e))
        print(traceback.format_exc())
        return jsonify({
            "error": "Internal server error",
            "message": str(e)
        }), 500

@app.route("/query")
def query_page():
    return render_template("query.html")

@app.route("/query_usage", methods=["GET"])
def query_usage():
    meter_id = request.args.get("meter_id")
    time_range = request.args.get("time_range")

    if not meter_id or not time_range:
        return jsonify({"error": "NO meter_id OR time_range"}), 400

    current_date = datetime.datetime.today()
    all_data = []

    if time_range == "today":
        date_str = current_date.strftime("%Y%m%d")
        all_data = load_meter_data(meter_id, [date_str])
    elif time_range == "last_7_days":
        dates = [(current_date - datetime.timedelta(days=i)).strftime("%Y%m%d") for i in range(7)]
        all_data = load_meter_data(meter_id, dates)
    elif time_range == "this_month":
        month_str = current_date.strftime("%Y%m")
        dates = [f"{month_str}{str(day).zfill(2)}" for day in range(1, current_date.day + 1)]
        all_data = load_meter_data(meter_id, dates)
    elif time_range == "last_month":
        first_day_last_month = (current_date.replace(day=1) - datetime.timedelta(days=1)).replace(day=1)
        last_month_str = first_day_last_month.strftime("%Y%m")
        last_day = (current_date.replace(day=1) - datetime.timedelta(days=1)).day
        dates = [f"{last_month_str}{str(day).zfill(2)}" for day in range(1, last_day + 1)]
        all_data = load_meter_data(meter_id, dates)
    else:
        return jsonify({"error": "invalid timeslot"}), 400

    if not all_data:
        return jsonify({"error": "not applicable"}), 404

    # Calculate electricity consumption per half hour/day
    df = pd.DataFrame(all_data, columns=["date", "time", "meter_ID", "meter_value"])
    df["meter_value"] = df["meter_value"].astype(float)
    df["datetime"] = pd.to_datetime(df["date"] + " " + df["time"])

    df.sort_values(by="datetime", inplace=True)
    df["usage"] = df["meter_value"].diff()

    if time_range == "today":
        df["time"] = df["datetime"].dt.strftime("%H:%M")
        result_df = df.groupby("time").agg({"usage": "sum"}).reset_index()
        x_labels = result_df["time"].tolist()
    else:
        result_df = df.groupby("date").agg({"usage": "sum"}).reset_index()
        x_labels = result_df["date"].tolist()

    y_values = result_df["usage"].fillna(0).tolist()

    return jsonify({"dates": x_labels, "usage": y_values})

def load_meter_data(meter_id, date_list):
    all_readings = []
    for date_str in date_list:
        month_folder = date_str[:6]  # YYYYMM
        file_path = os.path.join(data_dir, month_folder, f"readings_{date_str}.csv")
        if os.path.exists(file_path):
            df = pd.read_csv(file_path)
            df = df[df["meter_ID"] == meter_id]
            all_readings.extend(df.values.tolist())
    return all_readings

@app.route("/api/areas", methods=["GET"])
def get_areas():
    """Get area data from a JSON file."""
    area_data_file = os.path.join(app.static_folder, 'js', 'area_data.json')
    try:
        with open(area_data_file, 'r', encoding='utf-8') as f:
            return jsonify(json.load(f))
    except FileNotFoundError:
        return jsonify({"error": "Area data file not found"}), 404
    except json.JSONDecodeError:
        return jsonify({"error": "Invalid area data format"}), 500

@app.route('/reset')
def reset():
    """Reset the system."""
    if meter_system.reset_system():
        return """
        <script>
            alert('Reset Success!');
            window.location.href = '/';
        </script>
        """
    else:
        return """
        <script>
            alert('Reset Failed');
            window.location.href = '/';
        </script>
        """

if __name__ == "__main__":
    app.run()