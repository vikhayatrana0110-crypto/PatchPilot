import re
import logging
from langchain_core.tools import tool

logger = logging.getLogger(__name__)

@tool
def analyze_stack_trace(stack_trace: str) -> str:
    """
    Parses a Python stack trace to extract the error type, message, and
    the sequence of file calls.
    Args:
        stack_trace: The raw stack trace string to analyze.
    Returns:
        A structured string summarizing the error and the files/lines involved.
    """
    logger.info("Agent invoked analyze_stacke_trace tool.") 

    if not stack_trace or not stack_trace.strip():
        return "Error : No stack trace provided."

    lines = [line for line in stack_trace.strip().splitlines() if line.strip()]
    if not lines:
        return "Error: stack trace is empty"

    #in python tracebacks ,the error is alaways the last line
    last_line = lines[-1]
    error_type = "Unknown"
    error_message = "Unknown"

    if ":" in last_line:
        parts = last_line.split(":",1)
        error_type = parts[0].strip()
        error_message = parts[1].strip()
    else:
        error_type = last_line

    # Matches: file "filepath", line x, in function_name
    file_pattern =  re.compile(r'File "([^"]+)", line (\d+), in (.*)')

    calls = []
    for line in lines:
        match = file_pattern.search(line)
        if match:
            filepath,line_num,func_name = match.groups()
            calls.append({
                "file": filepath,
                "line": line_num,
                "function": func_name
            })

    if not calls:
        return (
            f"Error Type: {error_type}\n"
            f"Error Message: {error_message}\n"
            f"Note: Could not parse specific file paths from this stack trace format."
        )

    summary = [
        f"Error Type: {error_type}",
        f"Error Message: {error_message}",
        "\nCall Stack (most recent call last):"
    ]

    for i, call in enumerate(calls):
        summary.append(f" {i+1}. {call['function']} at {call['file']}:{call['line']}")

    summary.append(f"\n Suspected failiure location:  {calls[-1]['file']}    at line    {calls[-1]['line']}")

    return "\n".join(summary)