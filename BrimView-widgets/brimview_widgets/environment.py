import sys

running_from_pyodide = ("pyodide" in sys.modules)

def is_running_from_docker():
    """
        Check if the current panel application is running from a docker image
    """
    # Check if panel serve was passed 'from-docker' as one of the --args
    return 'from-docker' in sys.argv