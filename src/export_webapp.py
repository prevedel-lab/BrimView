from dotenv import load_dotenv
import os
from ftplib import FTP

load_dotenv() #read a .env file with FTP_HOST, FTP_USER, FTP_PASS

FTP_HOST = os.getenv("FTP_HOST")
FTP_USER = os.getenv("FTP_USER")
FTP_PASS = os.getenv("FTP_PASS")

LOCAL_FOLDER = "./pyodide"  # Export folder from build_webapp.py
REMOTE_DIR = "."  #Default folder is the correct one


def upload_files():
    try:
        # Connect to FTP server
        ftp = FTP(FTP_HOST)
        ftp.login(FTP_USER, FTP_PASS)
        print(f"Connected to {FTP_HOST}")

        # Change to the remote directory
        ftp.cwd(REMOTE_DIR)
        print(f"Changed to remote directory: {REMOTE_DIR}")

        # Loop over all files in local folder
        for filename in os.listdir(LOCAL_FOLDER):
            local_path = os.path.join(LOCAL_FOLDER, filename)

            # Skip directories
            if not os.path.isfile(local_path):
                continue

            with open(local_path, "rb") as f:
                ftp.storbinary(f"STOR {filename}", f)
                print(f"Uploaded: {filename}")

        ftp.quit()
        print("FTP session closed.")

    except Exception as e:
        print(f"Error: {e}")

if __name__ == "__main__":
    upload_files()
