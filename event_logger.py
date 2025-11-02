# event_logger.py
from datetime import datetime


def log_suite_start(gui, data):
    timestamp = datetime.now().strftime("%H:%M:%S")
    doc = data.doc.strip().replace("\n", " ") if data.doc else "(No documentation)"
    text = (
        f"[{timestamp}]  SUITE STARTED\n"
        f"  Name     : {data.name}\n"
        f"  Documentation: {doc}\n"
        f"{'-' * 60}\n"
    )
    _write(gui, text, "header")

def log_suite_end(gui, data, result):
    timestamp = datetime.now().strftime("%H:%M:%S")
    message = result.message.strip() if result.message else "(Empty)"
    text = (
        f"[{timestamp}] SUITE ENDED\n"
        f"  Name     : {data.name}\n"
        f"  Status   : {result.status}\n"
        f"  Message  : {message}\n"
        f"{'-' * 60}\n"
    )
    tag = "pass" if result.status.upper() == "PASS" else "fail"
    _write(gui, text, tag)

def log_test_start(gui, data):
    timestamp = datetime.now().strftime("%H:%M:%S")
    tags = ", ".join(data.tags or [])
    doc = data.doc.strip().replace("\n", " ") if data.doc else "(No documentation)"
    args = []
    try:
        args = [f"{k}={v}" for k, v in zip(data.args, data.arguments)]
    except:
        pass
    args_str = ", ".join(args) if args else "(No arguments)"

    text = (
        f"[{timestamp}] TEST STARTED\n"
        f"  Name       : {data.name}\n"
        f"  Tags       : {tags}\n"
        f"  Documentation: {doc}\n"
        f"  Arguments  : {args_str}\n"
        f"{'-' * 60}\n"
    )
    _write(gui, text, "header")


def log_test_end(gui, data, result):
    timestamp = datetime.now().strftime("%H:%M:%S")
    message = result.message.strip() if result.message else "(Empty)"
    text = (
        f"[{timestamp}] TEST ENDED\n"
        f"  Name     : {data.name}\n"
        f"  Status   : {result.status}\n"
        f"  Message  : {message}\n"
        f"{'-' * 60}\n"
    )
    tag = "pass" if result.status.upper() == "PASS" else "fail"
    _write(gui, text, tag)

def _write(gui, text, tag=None):
    def log_task():
        text_widget = gui.failure_text
        text_widget.configure(state='normal')

        # Insert log
        text_widget.insert("end", f"{_timestamp()} {text}\n", tag)

        # Limit number of lines
        try:
            max_lines = getattr(gui, "max_log_lines", 1000)
            current_line_count = int(text_widget.index('end-1c').split('.')[0])
            if current_line_count > max_lines:
                lines_to_delete = current_line_count - max_lines
                text_widget.delete("1.0", f"{lines_to_delete + 1}.0")
        except Exception as e:
            import logging
            logging.warning(f"Log trim error: {e}")

        text_widget.configure(state='disabled')
        text_widget.see("end")

    gui.root.after_idle(log_task)
def _timestamp():
    return datetime.now().strftime("[%H:%M:%S]")
