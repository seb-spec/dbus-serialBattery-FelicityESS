# This script is used to update single files from different submodules

import os
import requests
from urllib.parse import urlparse, unquote

files = [
    "https://raw.githubusercontent.com/victronenergy/velib_python/master/dbusmonitor.py",
    "https://raw.githubusercontent.com/victronenergy/velib_python/master/settingsdevice.py",
    "https://raw.githubusercontent.com/victronenergy/velib_python/master/ve_utils.py",
    "https://raw.githubusercontent.com/victronenergy/velib_python/master/vedbus.py",
]

root_dir = "./etc/dbus-serialbattery/ext"

print()
print("Updating submodules...")

for url in files:
    # Parse the URL to extract the path
    path = urlparse(url).path
    # print(f"path: {path}")

    # Decode URL encoding
    path = unquote(path)
    # print(f"path: {path}")

    # Split the path into parts
    parts = path.split("/")
    # print(f"parts: {parts}")

    # Extract the directory name (second subdirectory in the URL)
    directory_name = parts[2] if len(parts) > 2 else ""
    # print(f"directory_name: {directory_name}")

    # Create the directory if it doesn't exist
    if not os.path.exists(root_dir + "/" + directory_name):
        os.makedirs(root_dir + "/" + directory_name)

    # Extract the filename
    filename = parts[-1]
    # print(f"filename: {filename}")

    # Full path for the file to be saved
    file_path = os.path.join(root_dir + "/" + directory_name, filename)
    # print(f"file_path: {file_path}")

    # Download the file
    response = requests.get(url)
    response.raise_for_status()  # Raises an HTTPError if the response status code is 4XX/5XX

    # Write the file content to disk
    with open(file_path, "wb") as file:
        file.write(response.content)
    print(
        f"{directory_name}: file {filename} downloaded and saved in {root_dir}/{directory_name}/"
    )

print()
