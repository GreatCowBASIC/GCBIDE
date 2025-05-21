
import os
import subprocess

root_dir = "."  # Change to your project directory
excluded_dirs = {"build", "dist"}  # Set folders to exclude
files_to_include = []

# Walk through all subfolders and collect files
for foldername, subfolders, filenames in os.walk(root_dir):
    # Remove excluded directories from the list before iterating
    subfolders[:] = [subfolder for subfolder in subfolders if subfolder not in excluded_dirs]
    
    for filename in filenames:
        if filename.endswith((".png", ".json", ".txt", ".ico")):  # Modify as needed
            full_path = os.path.join(foldername, filename)
            relative_path = os.path.relpath(full_path, root_dir)
            
            if not "\\" in relative_path:
                relative_path = "."
            else:
                relative_path = relative_path.replace( filename, "")
                relative_path = relative_path.rstrip("\\")
            print(f"{relative_path}")
            files_to_include.append(f'--add-data "{full_path};{relative_path}"')

# input("Press Enter to continue...")

# Print the PyInstaller command
pyinstaller_command = "python -m PyInstaller --onefile " + " ".join(files_to_include) + " --noconsole --icon=app_icon.ico SuperIDEu.py"
print(pyinstaller_command)  # Copy and use this command

# Run the command
print("Running PyInstaller...")
subprocess.run(pyinstaller_command, shell=True)

print("Packaging complete! Check the 'dist' folder for the executable.")
