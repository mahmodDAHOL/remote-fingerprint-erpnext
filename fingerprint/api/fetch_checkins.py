import frappe
import datetime
import json
import os
import logging
from pickledb import PickleDB
from logging.handlers import RotatingFileHandler
from hrms.hr.doctype.employee_checkin.employee_checkin import add_log_based_on_employee_field



class dotdict(dict):
    """dot.notation access to dictionary attributes"""
    __getattr__ = dict.get
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__


def setup_logger(name, log_file, level=logging.INFO, formatter=None):
    
    if not formatter:
        formatter = logging.Formatter('%(asctime)s\t%(levelname)s\t%(message)s')

    handler = RotatingFileHandler(log_file, maxBytes=10000000, backupCount=50)
    handler.setFormatter(formatter)

    logger = logging.getLogger(name)
    logger.setLevel(level)
    if not logger.hasHandlers():
        logger.addHandler(handler)

    return logger


EMPLOYEE_NOT_FOUND_ERROR_MESSAGE = "No Employee found for the given employee field value"
EMPLOYEE_INACTIVE_ERROR_MESSAGE = "Transactions cannot be created for an Inactive Employee"
DUPLICATE_EMPLOYEE_CHECKIN_ERROR_MESSAGE = "This employee already has a log with the same timestamp"
allowlisted_errors = [EMPLOYEE_NOT_FOUND_ERROR_MESSAGE, EMPLOYEE_INACTIVE_ERROR_MESSAGE, DUPLICATE_EMPLOYEE_CHECKIN_ERROR_MESSAGE]


fing_config = {
    'ERPNEXT_VERSION': 14,
    'IMPORT_START_DATE':None,
    'LOGS_DIRECTORY': 'logs',
}

config = dotdict(fing_config)

device_punch_values_IN = getattr(fing_config, 'device_punch_values_IN', [0,4])
device_punch_values_OUT = getattr(fing_config, 'device_punch_values_OUT', [1,5])
ERPNEXT_VERSION = getattr(fing_config, 'ERPNEXT_VERSION', 14)

if not os.path.exists(config.LOGS_DIRECTORY):
    os.makedirs(config.LOGS_DIRECTORY)

error_logger = setup_logger('error_logger', '/'.join([config.LOGS_DIRECTORY, 'error.log']), logging.ERROR)
info_logger = setup_logger('info_logger', '/'.join([config.LOGS_DIRECTORY, 'logs.log']))
status = PickleDB('/'.join([config.LOGS_DIRECTORY, 'status.json']))
full_site_path = frappe.get_site_path()
full_site_path = os.path.abspath(frappe.get_site_path())

# def main():
#     try:
#         for file_name in os.listdir(full_site_path+'/private/files'):
#             file_path = os.path.join(full_site_path+'/private/files', file_name)
#             info_logger.info("Processing File: "+ file_path)
#             try:
#                 pull_process_and_push_data(file_path)
#                 info_logger.info("Successfully processed File: "+ file_path)
#             except:
#                 error_logger.exception('exception when calling pull_process_and_push_data function for file '+ file_path)
#             info_logger.info("Mission Accomplished!")
#     except:
#         error_logger.exception('exception has occurred in the main function...')

def sort_file_by_timestamp(file_path):
    # Read all lines from the file
    with open(file_path, 'r') as file:
        lines = file.readlines()

    # Parse each line, extract the timestamp, and store it with the line
    parsed_lines = []
    for line in lines:
        try:
            # Extract the JSON part of the line (last column)
            json_part = line.strip().split("\t")[-1]
            data = json.loads(json_part)

            # Parse the timestamp into a datetime object
            timestamp = datetime.strptime(data["timestamp"], "%Y-%m-%d %H:%M:%S")
            parsed_lines.append((timestamp, line))
        except Exception as e:
            print(f"Error parsing line: {line}. Error: {e}")

    # Sort the lines by the timestamp
    parsed_lines.sort(key=lambda x: x[0])

    # Write the sorted lines back to the file
    with open(file_path, 'w') as file:
        for _, line in parsed_lines:
            file.write(line)

def edit_attendance(record):
    record['timestamp'] = datetime.datetime.fromtimestamp(record['timestamp']) + datetime.timedelta(hours=3)
    return record

def pull_process_and_push_data(file_path, import_start_date, import_end_date):
    
    """ Takes a single device config as param and pulls data from that device.

    params:
    device: a single device config object from the local_config file
    device_attendance_logs: fetching from device is skipped if this param is passed. used to restart failed fetches from previous runs.
    """
    try:
        content = open(file_path, 'r').read()
    except:
        error_logger.exception(f"file {file_path} is not exist")
    attendances = json.loads(content)
    updated_attendances = [edit_attendance(att) for att in attendances]
    attendances = sorted(updated_attendances, key=lambda x: x['timestamp'])

    device_attendance_logs = attendances
    if not device_attendance_logs:
        return

    import_start_date = _safe_convert_date(import_start_date, '%Y-%m-%d')

    import_end_date = _safe_convert_date(import_end_date, '%Y-%m-%d')
    info_logger.info(f"{import_end_date=}")

    index_of_start = None
    index_of_end = None

    # Find start index
    for i, x in enumerate(device_attendance_logs):
        if x['timestamp'] >= import_start_date:
            index_of_start = i
            break

    # Find end index
    for i, x in enumerate(device_attendance_logs):
        if x['timestamp'] > import_end_date:
            index_of_end = i
            break

    # If end date not found, include all remaining logs
    if index_of_end is None:
        index_of_end = len(device_attendance_logs)

    # Process logs between start and end date
    for device_attendance_log in device_attendance_logs[index_of_start:index_of_end]:
        if device_attendance_log['punch'] in device_punch_values_OUT:
            punch_direction = 'OUT'
        elif device_attendance_log['punch'] in device_punch_values_IN:
            punch_direction = 'IN'
        else:
            punch_direction = None
        try:
            add_log_based_on_employee_field(
                device_attendance_log['user_id'], device_attendance_log['timestamp'], "device_id", punch_direction
            )

        except Exception as e:
            # error_logger.exception(f"Error fetching user {device_attendance_log['user_id']}: {e}")
            pass


def get_last_line_from_file(file):
    
    line = None
    if os.stat(file).st_size < 5000:
        # quick hack to handle files with one line
        with open(file, 'r') as f:
            for line in f:
                pass
    else:
        # optimized for large log files
        with open(file, 'rb') as f:
            f.seek(-2, os.SEEK_END)
            while f.read(1) != b'\n':
                f.seek(-2, os.SEEK_CUR)
            line = f.readline().decode()
    return line

def get_first_line_from_file(file):
    with open(file, 'r') as f:
        # Read the first line
        line = f.readline().strip()
        return line if line else None 


def get_dump_file_name_and_directory(device_id, device_ip):
    
    return 'logs' + '/' + device_id + "_" + device_ip.replace('.', '_') + '_last_fetch_dump.json'

def _safe_convert_date(datestring, pattern):
    
    try:
        return datetime.datetime.strptime(datestring, pattern)
    except:
        return None

@frappe.whitelist()
def fetch_checkins(import_start_date, import_end_date):
    req_files_found = False
    for file_name in os.listdir(full_site_path+'/private/files'):
        file_path = os.path.join(full_site_path+'/private/files', file_name)
        if file_path.endswith("_last_fetch_dump.json"):
            req_files_found = True
            info_logger.info("Processing File: "+ file_path)
            try:
                pull_process_and_push_data(file_path, import_start_date, import_end_date)
                info_logger.info("Successfully processed File: "+ file_path)

                # ✅ Delete the file after successful processing
                os.remove(file_path)
                info_logger.info(f"Deleted file after processing: {file_path}")
            except:
                error_logger.exception('exception when calling pull_process_and_push_data function for file '+ file_path)
            info_logger.info("Mission Accomplished!")
    if not req_files_found:
        error_logger.exception('No files found for bio devices, you should upload required json file.')
        frappe.throw("""No files found for bio devices, you should upload required json file,
                     to do that click 'Fetch & Upload' button then uncompress downloaded file,
                     then double click 'run_python.bat' file after connect your device with bio devices,
                     then wait until message appear that indicate files are uploaded.
                     """)