# gunicorn.conf.py

errorlog = 'gunicorn.log'         # Log errors here
accesslog = 'access.log'          # (Optional) Log HTTP access here
capture_output = True             # Capture stdout/stderr into log
loglevel = 'debug'                # 'info' or 'debug' for development
timeout = 60                      # Worker timeout in seconds

bind = "0.0.0.0:8080"

