# gunicorn.conf.py

# Logging
errorlog = 'gunicorn.log'         # Log uncaught errors and exceptions
accesslog = 'access.log'          # HTTP access log (optional but helpful)
capture_output = True             # Capture print() and stdout/stderr
loglevel = 'debug'                # Use 'debug' for development, 'info' for prod

# Server behavior
timeout = 60                      # Worker timeout in seconds (60 is typical)

# Bind to all interfaces (use with caution on public servers)
bind = "0.0.0.0:8080"
