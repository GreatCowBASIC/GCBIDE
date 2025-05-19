import psutil
import os
import subprocess
import tkinter as tk
from tkinter import messagebox
import string
import win32file
import win32com.client
import logging
import argparse

def setup_logging(debug=False):
    """Configure logging based on debug parameter."""
    level = logging.DEBUG if debug else logging.ERROR
    logging.basicConfig(level=level, format='%(asctime)s - %(levelname)s - %(message)s')

def is_eligible_drive(drive):
    """Check if a drive is non-system and either DRIVE_FIXED, DRIVE_REMOVABLE, DRIVE_CDROM, or USB."""
    try:
        # Remove trailing backslash
        drive_path = drive.rstrip('\\')
        # Get drive type using win32file
        drive_type = win32file.GetDriveType(drive_path)
        logging.debug(f"Drive {drive}: Type {drive_type}")

        # Exclude system drive
        system_drive = os.environ.get('SystemDrive', 'C:').rstrip('\\')
        if drive_path.upper() == system_drive.upper():
            logging.debug(f"Drive {drive}: Excluded (system drive)")
            return False

        # Check psutil for network drives and drive properties
        partitions = psutil.disk_partitions()
        is_network = False
        opts = ""
        for partition in partitions:
            if partition.device.startswith(drive):
                is_network = 'remote' in partition.opts.lower()
                opts = partition.opts
                logging.debug(f"Drive {drive}: psutil opts={opts}, is_network={is_network}")
                if is_network:
                    logging.debug(f"Drive {drive}: Excluded (network drive)")
                    return False
                break
        else:
            logging.debug(f"Drive {drive}: No matching psutil partition found")

        # Accept DRIVE_REMOVABLE, DRIVE_CDROM, or DRIVE_FIXED
        if drive_type in (win32file.DRIVE_REMOVABLE, win32file.DRIVE_CDROM, win32file.DRIVE_FIXED):
            logging.debug(f"Drive {drive}: Accepted (type {drive_type})")
            return True

        # Check if it's a USB drive via WMI
        wmi = win32com.client.GetObject("winmgmts:")
        drive_letter = drive_path[0].upper()
        disks = wmi.ExecQuery("SELECT * FROM Win32_DiskDrive")
        for disk in disks:
            is_usb = disk.PNPDeviceID and 'USB' in disk.PNPDeviceID.upper()
            is_removable = disk.MediaType and 'removable' in disk.MediaType.lower()
            if is_usb or is_removable:
                partitions = wmi.ExecQuery(f"ASSOCIATORS OF {{Win32_DiskDrive.DeviceID='{disk.DeviceID}'}} WHERE AssocClass=Win32_DiskDriveToDiskPartition")
                for partition in partitions:
                    logical_disks = wmi.ExecQuery(f"ASSOCIATORS OF {{Win32_DiskPartition.DeviceID='{partition.DeviceID}'}} WHERE AssocClass=Win32_LogicalDiskToPartition")
                    for logical_disk in logical_disks:
                        if logical_disk.DeviceID.startswith(drive_letter):
                            logging.debug(f"Drive {drive}: Confirmed as USB/removable (PNPDeviceID={disk.PNPDeviceID}, MediaType={disk.MediaType})")
                            return True
        logging.debug(f"Drive {drive}: Not identified as USB/removable via WMI")
        logging.debug(f"Drive {drive}: Excluded (not eligible type or USB)")
        return False
    except Exception as e:
        logging.error(f"Error checking drive {drive}: {e}")
        return False

def find_superide():
    """Search eligible drives for superide.bat (case-insensitive) and execute it if found."""
    # Get all possible drive letters
    drives = [f"{d}:\\" for d in string.ascii_uppercase if os.path.exists(f"{d}:\\")]
    logging.debug(f"Found drives: {drives}")
    
    # Filter for eligible drives
    eligible_drives = [drive for drive in drives if is_eligible_drive(drive)]
    logging.debug(f"Eligible drives: {eligible_drives}")
    
    # Search each eligible drive for superide.bat
    for drive in eligible_drives:
        logging.debug(f"Searching drive {drive}")
        try:
            for root, _, files in os.walk(drive):
                logging.debug(f"Checking directory: {root}")
                # Case-insensitive check for superide.bat
                for file in files:
                    if file.lower() == 'superide.bat':
                        batch_file = os.path.join(root, file)
                        logging.info(f"Found {file} at {batch_file}")
                        try:
                            # Execute the batch file
                            subprocess.run(batch_file, shell=True, check=True)
                            logging.info(f"Successfully executed {batch_file}")
                            return True
                        except subprocess.CalledProcessError as e:
                            logging.error(f"Failed to execute {batch_file}: {e}")
                            root = tk.Tk()
                            root.withdraw()
                            messagebox.showerror("Error", f"Failed to execute {file} at {batch_file}")
                            root.destroy()
                            return True
        except Exception as e:
            logging.error(f"Error accessing drive {drive}: {e}")
            continue
    logging.info("superide.bat not found on any eligible drive")
    return False

def show_not_found_popup():
    """Show a pop-up message if superide.bat is not found."""
    root = tk.Tk()
    root.withdraw()
    messagebox.showinfo("Not Found", "SuperIDE not found")
    root.destroy()

def main():
    """Main function to run the script."""
    # Parse command-line arguments
    parser = argparse.ArgumentParser(description="Search for and execute superide.bat on eligible drives.")
    parser.add_argument('--debug', action='store_true', help="Enable debug logging")
    args = parser.parse_args()

    # Set up logging based on debug flag
    setup_logging(debug=args.debug)

    if not find_superide():
        show_not_found_popup()

if __name__ == "__main__":
    main()