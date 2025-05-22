import os
import subprocess
import time
import signal
from board import SCL, SDA
import busio
from gpiozero import RotaryEncoder, Button, DigitalInputDevice
from oled_text import OledText
import re # Regular expression operations

# --- Project Settings ---
WIFI_PASSWORD = "qw112233!!"
START_BUTTON_GPIO = 16
STOP_BUTTON_GPIO = 26
ROTARY_ENCODER_A_GPIO = 17  # Change according to actual connection
ROTARY_ENCODER_B_GPIO = 18  # Change according to actual connection
ROTARY_ENCODER_BUTTON_GPIO = 27 # Change according to actual connection

# --- Global Variables ---
oled = None
encoder = None
button = None
start_switch = None
stop_switch = None

current_page_title = "APs" # Initial page
ap_list = []
selected_ap_index = 0
scroll_offset_ap = 0
connection_status = "Not Started" # Initial status
device_hostname = "RPi0-XXXX" # Will be set dynamically
wlx_interface = None # Will be set dynamically

project_running = False

# --- OLED Display Functions ---
def init_oled():
    """Initializes the OLED display."""
    global oled
    try:
        i2c = busio.I2C(SCL, SDA)
        oled = OledText(i2c, 128, 64)
        oled.clear()
        oled.text("System Ready", 1)
        oled.text("Press GPIO {} to".format(START_BUTTON_GPIO), 2)
        oled.text("start project.", 3)
    except Exception as e:
        print(f"ERROR: OLED initialization failed: {e}")
        # To proceed without OLED, an alternative output method can be added here.
        # Left oled as None for now to prevent the program from running without OLED.
        oled = None

def display_ap_page():
    """Displays the APs page on the OLED screen."""
    global oled, ap_list, selected_ap_index, scroll_offset_ap, current_page_title
    if not oled: return
    oled.clear()
    current_page_title = "APs"
    oled.text(f">{current_page_title}" if current_page_title == "APs" else "APs", 1)

    if not ap_list:
        oled.text("No APs found", 2)
        oled.text("or filtered", 3)
        return

    # Scrolling logic Display 4 APs per page (excluding the title line)
    displayable_aps = ap_list[scroll_offset_ap : scroll_offset_ap + 4]

    for i, ap_name in enumerate(displayable_aps):
        line_num = i + 2 # Start after the title line
        prefix = ">" if (i + scroll_offset_ap) == selected_ap_index else " "
        oled.text(f"{prefix}{ap_name[:15]}", line_num) # Limit SSID to 15 characters

def display_status_page():
    """Displays the Status page on the OLED screen."""
    global oled, device_hostname, connection_status, current_page_title
    if not oled: return
    oled.clear()
    current_page_title = "STATUS"
    oled.text(f">{current_page_title}" if current_page_title == "STATUS" else "STATUS", 1)
    oled.text(f"H:{device_hostname}", 2) # Hostname
    oled.text(f"S:{connection_status[:14]}", 3) # Connection status (max 14 characters)

# --- Network Functions ---
def get_wlx_interface():
    """Finds the wireless network interface starting with 'wlx'."""
    global wlx_interface
    try:
        result = subprocess.check_output("ls /sys/class/net/", shell=True).decode("utf-8")
        interfaces = result.split()
        for iface in interfaces:
            if iface.startswith("wlx"):
                wlx_interface = iface
                print(f"USB WiFi interface to be used: {wlx_interface}")
                return wlx_interface
    except Exception as e:
        print(f"ERROR: WiFi interface not found: {e}")
    return None

def set_hostname():
    """Sets the device hostname."""
    global device_hostname, wlx_interface
    if wlx_interface:
        # Get the last 4 characters of the wlx interface name (e.g., wlx788cb58b0782 -> 0782)
        # Remove characters like ":" if they exist.
        last_chars = re.sub(r'[^a-zA-Z0-9]', '', wlx_interface)[-4:]
        device_hostname = f"RPi0-{last_chars}"
        try:
            # Set hostname in the system (may require sudo, run the script with sudo)
            subprocess.run(f"sudo hostnamectl set-hostname {device_hostname}", shell=True, check=True)
            print(f"Hostname set to: {device_hostname}")
        except subprocess.CalledProcessError as e:
            print(f"ERROR: Failed to set hostname: {e}")
            device_hostname = "RPi0-ERR" # Default in case of error
    else:
        print("WARNING: Could not set hostname because wlx interface was not found.")
        device_hostname = "RPi0-NOIF"

def clear_existing_wifi_connections():
    """Removes all existing WiFi connections from NetworkManager."""
    global wlx_interface
    if not wlx_interface:
        print("WARNING: Cannot clear connections without a WiFi interface.")
        return
    try:
        print("Clearing existing WiFi connections...")
        # Find active connections
        result = subprocess.check_output(f"nmcli -t -f NAME,DEVICE c show --active", shell=True).decode("utf-8")
        active_connections = []
        for line in result.splitlines():
            parts = line.split(':')
            if len(parts) == 2 and parts[1] == wlx_interface:
                active_connections.append(parts[0])

        # Deactivate active connections
        for conn_name in active_connections:
            print(f"Deactivating connection '{conn_name}'...")
            subprocess.run(f"nmcli c down '{conn_name}'", shell=True, check=False) # Continue even if it fails

        # List all saved connections (including those not belonging to interface starting with wlx)
        # Warning: This command will delete saved connections for other interfaces too.
        result = subprocess.check_output("nmcli -t -f UUID,TYPE c", shell=True).decode("utf-8")
        for line in result.splitlines():
            uuid, type = line.split(':')
            if type == "802-11-wireless": # Delete only WiFi connections
                print(f"Deleting WiFi connection with UUID '{uuid}'...")
                subprocess.run(f"nmcli c delete uuid {uuid}", shell=True, check=False)
        print("WiFi connections cleared.")
    except Exception as e:
        print(f"ERROR: An issue occurred while clearing WiFi connections: {e}")

def scan_wifi_networks():
    """Scans for nearby WiFi networks using the USB WiFi adapter and lists those starting with 'QW-'."""
    global ap_list, wlx_interface, connection_status, selected_ap_index, scroll_offset_ap
    if not wlx_interface:
        print("WARNING: Cannot scan without a WiFi interface.")
        ap_list = ["No Interface"]
        selected_ap_index = 0
        scroll_offset_ap = 0
        return

    print("Scanning WiFi networks...")
    connection_status = "Scanning..."
    if current_page_title == "STATUS": # If on status page and scanning, show it
        display_status_page()
    elif current_page_title == "APs":
        oled.clear()
        oled.text(f">{current_page_title}", 1)
        oled.text("Scanning...", 2)


    temp_ap_list = []
    try:
        # Force NetworkManager to rescan
        subprocess.run(f"nmcli dev wifi rescan ifname {wlx_interface}", shell=True, check=True, timeout=15)
        # Get scan results (SSID, SIGNAL, SECURITY)
        # SSIDs can contain spaces, parse carefully.
        # -t (terse) mode separates fields with ':'.
        # With -f (fields), we only get SSID.
        # --escape no prevents escaping of special characters.
        result = subprocess.check_output(f"nmcli --escape no -t -f SSID dev wifi list ifname {wlx_interface}", shell=True, timeout=10).decode("utf-8")
        raw_ssids = result.strip().split('\n')
        
        # Remove duplicate SSIDs and empty lines, then filter
        unique_filtered_ssids = []
        seen_ssids = set()
        for ssid in raw_ssids:
            ssid = ssid.strip() # Trim leading/trailing whitespace
            if ssid and ssid.startswith("QW-") and ssid not in seen_ssids:
                unique_filtered_ssids.append(ssid)
                seen_ssids.add(ssid)
        ap_list = unique_filtered_ssids
        print(f"Found and filtered APs: {ap_list}")

    except subprocess.TimeoutExpired:
        print("ERROR: WiFi scan timed out.")
        ap_list = ["Scan Error"]
    except Exception as e:
        print(f"ERROR: An issue occurred during WiFi scan: {e}")
        ap_list = ["Scan Error"]

    selected_ap_index = 0
    scroll_offset_ap = 0
    if not ap_list:
        ap_list = ["No QW- APs"] # If no APs remain after filtering

    # Update status after scan
    if connection_status == "Scanning...": # If not yet connected
        connection_status = "Scanned/Select" # Scan finished, waiting for selection
    display_ap_page() # Update AP page

def connect_to_wifi(ssid):
    """Attempts to connect to the specified SSID with the WiFi password."""
    global connection_status, wlx_interface
    if not wlx_interface:
        print("WARNING: Cannot connect without a WiFi interface.")
        connection_status = "No Interface"
        display_status_page()
        return

    connection_status = "Connecting..."
    display_status_page() # Show "Connecting..." on OLED
    print(f"Connecting to network '{ssid}'...")

    try:
        # First, disconnect the current connection (if any)
        subprocess.run(f"nmcli dev disconnect {wlx_interface}", shell=True, check=False)
        time.sleep(1) # Short wait for the interface to become free

        # Establish the new connection
        # The --wait parameter waits until the connection is complete (default 30 seconds)
        # nmcli dev wifi connect "SSID" password "PASSWORD" ifname wlx...
        connect_command = f"nmcli dev wifi connect \"{ssid}\" password \"{WIFI_PASSWORD}\" ifname {wlx_interface}"
        process = subprocess.Popen(connect_command, shell=True, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = process.communicate(timeout=45) # Wait 45 seconds for connection
        stdout_str = stdout.decode('utf-8', errors='ignore')
        stderr_str = stderr.decode('utf-8', errors='ignore')

        if process.returncode == 0 and "successfully activated" in stdout_str:
            print(f"Successfully connected to '{ssid}'.")
            # Get IP address
            ip_result = subprocess.check_output(f"nmcli -g IP4.ADDRESS dev show {wlx_interface}", shell=True).decode("utf-8").strip()
            # Remove CIDR notation (e.g., 192.168.1.110/24 -> 192.168.1.110)
            ip_address = ip_result.split('/')[0] if '/' in ip_result else ip_result
            if not ip_address: # Sometimes IP is not immediately available, try again
                time.sleep(3)
                ip_result = subprocess.check_output(f"nmcli -g IP4.ADDRESS dev show {wlx_interface}", shell=True).decode("utf-8").strip()
                ip_address = ip_result.split('/')[0] if '/' in ip_result else ip_result

            if ip_address:
                connection_status = ip_address
            else:
                connection_status = "No IP Acquired"
        else:
            print(f"ERROR: Failed to connect to '{ssid}'.")
            print(f"nmcli stdout: {stdout_str}")
            print(f"nmcli stderr: {stderr_str}")
            connection_status = "Not Connected"
    except subprocess.TimeoutExpired:
        print(f"ERROR: Connection to '{ssid}' timed out.")
        connection_status = "Timeout"
    except Exception as e:
        print(f"ERROR: During WiFi connection: {e}")
        connection_status = "Error Occurred"
    
    display_status_page()

def disconnect_wifi():
    """Disconnects the current WiFi connection."""
    global connection_status, wlx_interface
    if not wlx_interface:
        print("WARNING: Cannot disconnect without a WiFi interface.")
        return
    if connection_status not in ["Not Connected", "Not Started", "Scanned/Select", "No Interface", "Scanning..."] and not connection_status.startswith("RPi0"): # If it's an IP address or connecting
        print("Disconnecting WiFi...")
        try:
            subprocess.run(f"nmcli dev disconnect {wlx_interface}", shell=True, check=True)
            print("WiFi disconnected.")
        except Exception as e:
            print(f"ERROR: While disconnecting WiFi: {e}")
    connection_status = "Not Connected" # Update status

# --- Rotary Encoder and Button Functions ---
def setup_gpio():
    """Sets up GPIO pins and event handlers."""
    global encoder, button, start_switch, stop_switch
    # Rotary Encoder
    encoder = RotaryEncoder(a=ROTARY_ENCODER_A_GPIO, b=ROTARY_ENCODER_B_GPIO, max_steps=0)
    encoder.when_rotated = handle_rotation
    # Rotary Encoder Button
    button = Button(ROTARY_ENCODER_BUTTON_GPIO, pull_up=True, bounce_time=0.1) # pull_up might be True, depends on hardware
    button.when_pressed = handle_click
    # Start and Stop Buttons
    start_switch = Button(START_BUTTON_GPIO, pull_up=True, bounce_time=0.2)
    start_switch.when_pressed = start_project_action

    stop_switch = Button(STOP_BUTTON_GPIO, pull_up=True, bounce_time=0.2)
    stop_switch.when_pressed = stop_project_action

def handle_rotation():
    """Called when the rotary encoder is rotated."""
    global selected_ap_index, scroll_offset_ap, ap_list, current_page_title, project_running
    if not project_running or not oled: return

    delta = round(encoder.steps) # Get full steps
    encoder.steps = 0  # Reset for the next delta

    if current_page_title == "APs":
        if not ap_list: return
        max_index = len(ap_list) -1 # 0-based indexing
        
        new_index = selected_ap_index + delta
        selected_ap_index = max(0, min(new_index, max_index))

        # Scrolling logic (display 4 lines)
        if selected_ap_index < scroll_offset_ap:
            scroll_offset_ap = selected_ap_index
        elif selected_ap_index >= scroll_offset_ap + 4:
            scroll_offset_ap = selected_ap_index - 3
        
        # Ensure scroll_offset_ap is within valid range
        scroll_offset_ap = max(0, min(scroll_offset_ap, len(ap_list) - 4 if len(ap_list) > 4 else 0))

        display_ap_page()

    elif current_page_title == "STATUS":
        # No rotation action on the status page (for now)
        pass

def handle_click():
    """Called when the rotary encoder button is pressed."""
    global current_page_title, ap_list, selected_ap_index, project_running, connection_status
    if not project_running or not oled: return

    if current_page_title == "APs":
        # Connect to selected AP or page changed
        if selected_ap_index == -1 : # If title is selected (no title selection in this example, direct AP list)
            # Page change logic could be added here, but clicking on APs page always means AP selection currently.
            pass
        elif ap_list and 0 <= selected_ap_index < len(ap_list):
            selected_ssid = ap_list[selected_ap_index]
            if selected_ssid not in ["Scan Error", "No QW- APs", "No Interface"]:
                print(f"Selected AP: {selected_ssid}")
                current_page_title = "STATUS" # Switch to Status page while attempting connection
                connect_to_wifi(selected_ssid) # This function will call display_status_page
            else:
                print("Invalid AP selection.")
        else:
            print("No AP to select or index error.")

    elif current_page_title == "STATUS":
        # Clicking on Status page returns to APs page and rescans
        print("Returning to APs page and rescanning...")
        disconnect_wifi() # Disconnect current connection
        current_page_title = "APs"
        # connection_status = "Rescanning" # Before scan starts
        # display_ap_page() # Could show "Rescanning..." message
        scan_wifi_networks() # This function will call display_ap_page

# --- Project Flow ---
def start_project_action():
    """Called when the project start switch is activated."""
    global project_running, wlx_interface, oled
    if project_running:
        print("Project is already running.")
        return
    
    print("Starting project...")
    if not oled:
        init_oled() # Try to init OLED again if not initialized
        if not oled:
            print("CRITICAL ERROR: Cannot start project without OLED display.")
            return # Do not start if OLED is not available
            
    project_running = True
    oled.clear()
    oled.text("Project Starting", 1)

    if not wlx_interface:
        wlx_interface = get_wlx_interface()
    
    if not wlx_interface:
        oled.clear()
        oled.text("ERROR:",1)
        oled.text("No USB WiFi!",2)
        project_running = False # Stop project if no wlx interface
        time.sleep(3)
        init_oled() # Return to initial screen
        return

    set_hostname() # Set the hostname
    clear_existing_wifi_connections() # Clear old connections
    
    # Show AP page and scan at startup
    global current_page_title, connection_status
    current_page_title = "APs"
    connection_status = "Initial Scan"
    scan_wifi_networks() # This will call display_ap_page

def stop_project_action():
    """Called when the project stop switch is activated."""
    global project_running, oled
    if not project_running:
        print("Project is already stopped.")
        return

    print("Stopping project...")
    project_running = False
    disconnect_wifi()
    if oled:
        oled.clear()
        oled.text("Project Stopped.", 1)
        time.sleep(2)
        # Return to the initial "System Ready" screen
        init_oled()
    # Releasing GPIO resources is preferred but gpiozero usually handles this at script end.
    # If needed: encoder.close(); button.close(); start_switch.close(); stop_switch.close()
    print("Project stopped. Press GPIO {} to restart.".format(START_BUTTON_GPIO))


# --- Main Program ---
def main():
    """Main program loop."""
    print("Raspberry Pi WiFi Manager Project Started.")
    print(f"Use GPIO {START_BUTTON_GPIO} switch to start.")
    print(f"Use GPIO {STOP_BUTTON_GPIO} switch to stop.")

    init_oled() # Initialize OLED at program start
    setup_gpio() # Setup GPIO pins

    try:
        # Use signal.pause() to keep the program running.
        # Events (button presses, encoder rotations) will be handled in the background.
        signal.pause()
    except KeyboardInterrupt:
        print("\nExiting with Ctrl+C...")
    finally:
        if project_running: # If exiting while project is running
            disconnect_wifi()
        if oled:
            oled.clear()
            oled.text("Goodbye!", 1)
            time.sleep(1)
            oled.clear()
        print("Program terminated.")

if __name__ == "__main__":
    # Disable onboard WiFi interface wlan0 (if desired and script is run with sudo)
    # This operation can be permanent, be careful. 'rfkill block wifi' is a temporary solution.
    # Or it can be done via /etc/network/interfaces or NetworkManager settings.
    # Just as a warning:
    # os.system("sudo ifconfig wlan0 down") # Temporarily disables
    # os.system("sudo rfkill block wifi") # Can block all WiFi (including Bluetooth)

    # Important: This script may need to be run with sudo for NetworkManager commands
    # (hostname setting, connection deletion, etc.) and potentially to disable wlan0.
    # Use `sudo /home/<USERNAME>/<ENVIRONMENT_NAME>/bin/python3 /home/<USERNAME>/wifi_scan_connect.py` if python virtual environment is used
    # Use `sudo python3 this_script_name.py` if not
    main()
