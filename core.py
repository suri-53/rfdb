import threading
import ast
import logging
import time
import tkinter as tk
from robot.libraries.BuiltIn import BuiltIn
from datetime import datetime
from copy import deepcopy

class SimpleRetryCore:
    ROBOT_LISTENER_API_VERSION = 3
    GUI_TIMEOUT_SECONDS = 300  # 5 minutes max wait for GUI response
    MAX_SEEN_KEYWORDS = 500  # Limit tracked keywords to prevent unbounded growth

    def __init__(self):
        self.builtin = BuiltIn()
        self.failed_keyword = None
        self.current_test = None
        self.current_suite = None
        self.continue_event = threading.Event()
        self.retry_success = False
        self.abort_suite = False
        self.gui_controller = None
        self.skip_test = False
        self.skip_keyword = False
        self.call_stack = []
        self.keyword_stack = []
        
        # Test start control
        self.test_start_event = threading.Event()
        self._test_started = False
        
        # Keyword ignore functionality with memory limits
        self.ignored_keywords = set()  # Exact keyword names to ignore
        self.seen_keywords = set()  # Track all keywords seen during execution
        self._seen_keywords_queue = []  # Track insertion order for LRU eviction

        raw_mutes = {
            "Run Keyword And Ignore Error",
            "Run Keyword And Expect Error",
            "Run Keyword And Return Status",
            "Run Keyword And Warn On Failure"
        }
        self.muting_keywords = {kw.strip().lower() for kw in raw_mutes}

        logging.basicConfig(
            filename="retry_debug.log",
            level=logging.INFO,
            format="%(asctime)s - %(levelname)s - %(message)s"
        )

    def start_suite(self, data, result):
        self.current_suite = data.name
        
        # Wait for user to click Start button (only once per execution)
        if not self._test_started:
            self._test_started = True
            logging.info(f"Suite ready: {self.current_suite}. Waiting for user to start...")
            
            if self.gui_controller and getattr(self.gui_controller, "gui_ready", False):
                self.gui_controller.show_ready_state(data.name)
            
            # Block until user clicks Start button
            self.test_start_event.wait()
            logging.info("User started test execution")
            
            if self.gui_controller:
                self.gui_controller.show_running_state()
        
        logging.info(f"Suite started: {self.current_suite}")
        if self.gui_controller and getattr(self.gui_controller, "gui_ready", False):
            if hasattr(self.gui_controller, "log_suite_start"):
                self.gui_controller.log_suite_start(data)

    def end_suite(self, data, result):
        if self.gui_controller and getattr(self.gui_controller, "gui_ready", False):
            self.gui_controller.update_status("Suite finished", "green")

            if hasattr(self.gui_controller, "log_suite_end"):
                self.gui_controller.log_suite_end(data, result)

            # ✅ Safely close GUI after delay
            def ask_to_close():
                from tkinter import messagebox
                if messagebox.askyesno("Test Finished", "Close the debugger?"):
                    try:
                        self.gui_controller.root.after(0, self.gui_controller.root.quit)
                    except Exception as e:
                        logging.warning(f"Safe GUI shutdown failed: {e}")

            self.gui_controller.root.after(1000, ask_to_close)

    def start_test(self, data, result):
        self.current_test = data.name
        self.skip_test = False  # Reset skip flag for new test
        logging.info(f"Test started: {self.current_test}")

        # Stack view
        if self.gui_controller:
            if hasattr(self.gui_controller, "start_test_stack_root"):
                self.gui_controller.start_test_stack_root(data.name)

            # Logging the test start with fallback delay
            if getattr(self.gui_controller, "gui_ready", False):
                if hasattr(self.gui_controller, "log_test_start"):
                    self.gui_controller.log_test_start(data)
            else:
                def delayed_log():
                    if hasattr(self.gui_controller, "log_test_start"):
                        self.gui_controller.log_test_start(data)

                threading.Timer(0.5, delayed_log).start()

    def end_test(self, data, result):
        # If skip_test was triggered, mark test as failed but continue to next test
        if self.skip_test:
            result.status = 'FAIL'
            result.message = 'Test skipped by user'
            self.skip_test = False
            logging.info(f"Test '{data.name}' was skipped by user - moving to next test")
        
        logging.info(f"Test ended: {data.name} | Status: {result.status}")

        if self.gui_controller:
            if getattr(self.gui_controller, "gui_ready", False):
                if hasattr(self.gui_controller, "log_test_end"):
                    self.gui_controller.log_test_end(data, result)
            else:
                def delayed_log():
                    if hasattr(self.gui_controller, "log_test_end"):
                        self.gui_controller.log_test_end(data, result)

                threading.Timer(0.5, delayed_log).start()

    def start_keyword(self, data, result):
        # Store full keyword data object for accurate trace
        self.keyword_stack.append(data)
        
        # Track all keywords seen during execution with memory limit
        if hasattr(data, 'name'):
            keyword_name = data.name
            
            # Add only if not already present
            if keyword_name not in self.seen_keywords:
                self.seen_keywords.add(keyword_name)
                self._seen_keywords_queue.append(keyword_name)
                
                # Enforce memory limit using LRU eviction
                if len(self.seen_keywords) > self.MAX_SEEN_KEYWORDS:
                    oldest = self._seen_keywords_queue.pop(0)
                    self.seen_keywords.discard(oldest)
                    logging.debug(f"[Debugger] Evicted oldest keyword from seen_keywords: {oldest}")

    from copy import deepcopy
    from uuid import uuid4

    def end_keyword(self, data, result):
        current_kw = self.keyword_stack[-1] if self.keyword_stack else data
        normalized_name = self._normalize_keyword_name(current_kw.name)

        # 🔍 Check if keyword is in user's ignore list (case-insensitive, applies to entire execution)
        # Convert failure to PASS so test continues without interruption
        normalized_ignored = {self._normalize_keyword_name(kw) for kw in self.ignored_keywords}
        if normalized_name in normalized_ignored and result.status == 'FAIL':
            result.status = 'PASS'
            result.message = f"[Ignored by debugger] Original failure: {result.message}"
            logging.info(f"[Debugger] Auto-ignored failure in '{current_kw.name}' - marked as PASS")
            if self.keyword_stack:
                self.keyword_stack.pop()
            return

        # 🔍 Check for muting wrapper in parent keywords
        muted_parent = next(
            (kw.name for kw in reversed(self.keyword_stack[:-1])
             if self._normalize_keyword_name(kw.name) in self.muting_keywords),
            None
        )

        # ✅ If failure is inside wrapper, skip GUI but let Robot handle it
        if result.status == 'FAIL' and muted_parent:
            logging.info(f"[Debugger] Ignoring failure inside wrapper '{muted_parent}'. Robot will handle it.")
            if self.keyword_stack:
                self.keyword_stack.pop()
            return

        # ❌ Abort logic
        if self.abort_suite:
            result.status = 'FAIL'
            result.message = 'Suite aborted by user'
            logging.warning("Suite aborted by user.")
            if self.keyword_stack:
                self.keyword_stack.pop()
            return

        # ❌ Skip test logic - skip all remaining keywords in current test
        if self.skip_test:
            # Convert to PASS so remaining keywords are skipped without blocking
            result.status = 'PASS'
            result.message = 'Keyword skipped (test skip in progress)'
            logging.info(f"Skipping keyword '{current_kw.name}' - test skip in progress")
            if self.keyword_stack:
                self.keyword_stack.pop()
            return

        # 🧠 Handle real failures
        if result.status == 'FAIL' and not self.retry_success:
            self.failed_keyword = deepcopy(current_kw)
            self.failed_stack_snapshot = deepcopy(self.keyword_stack)

            if self.gui_controller and getattr(self.gui_controller, "gui_ready", False):

                # ✅ Setup/Teardown → async show, do not block
                if "setup" in normalized_name or "teardown" in normalized_name:
                    logging.info(
                        f"[Debugger] Setup/Teardown failure in '{current_kw.name}' → showing GUI async (no block)")
                    threading.Thread(
                        target=lambda: self.gui_controller.show_failure(
                            suite=self.current_suite,
                            test=self.current_test,
                            keyword=current_kw.name,
                            message=result.message or "(No failure message)",
                            args=current_kw.args,
                            call_stack=self.failed_stack_snapshot
                        ),
                        daemon=True
                    ).start()
                    if self.keyword_stack:
                        self.keyword_stack.pop()
                    return

                # ✅ Normal failure → show GUI and block Robot until user acts
                def show_failure_and_wait():
                    self.gui_controller.show_failure(
                        suite=self.current_suite,
                        test=self.current_test,
                        keyword=current_kw.name,
                        message=result.message or "(No failure message)",
                        args=current_kw.args,
                        call_stack=self.failed_stack_snapshot
                    )

                self.continue_event.clear()
                self.gui_controller.root.after(0, show_failure_and_wait)

                # 🔒 Block Robot but keep GUI responsive
                while not self.continue_event.is_set():
                    try:
                        self.gui_controller.root.update()
                        time.sleep(0.01)  # Prevent busy waiting and reduce CPU usage
                    except tk.TclError as e:
                        logging.error(f"[Debugger] GUI destroyed: {e} - auto-continuing")
                        self.continue_event.set()
                        break
                    except Exception as e:
                        logging.error(f"GUI update error during wait: {e}")
                        # Don't break on transient errors, only on critical ones
                        time.sleep(0.1)  # Back off a bit

                # ✅ Handle Skip and Retry actions after unblock
                if self.skip_keyword:
                    result.status = 'PASS'
                    result.message = f"[DEBUGGER OVERRIDE] Keyword '{self.failed_keyword.name}' was skipped by user."
                    # Note: BuiltIn() calls are safe here - we're back in Robot's thread after wait
                    try:
                        self.builtin.log(f"[Debugger] Skipped keyword: {self.failed_keyword.name}", "WARN")
                        self.builtin.set_tags("debugger-skipped")
                    except Exception as e:
                        logging.warning(f"Failed to log/set tag for skipped keyword: {e}")
                    self.skip_keyword = False
                    self.failed_keyword = None
                    return

                if self.retry_success:
                    result.status = 'PASS'
                    result.message = f"[RETRIED SUCCESSFULLY] Keyword '{self.failed_keyword.name}' passed after GUI retry."
                    # Note: BuiltIn() calls are safe here - we're back in Robot's thread after wait
                    try:
                        self.builtin.log(f"[Debugger] Retried keyword succeeded: {self.failed_keyword.name}", "INFO")
                        self.builtin.set_tags("debugger-retried")
                    except Exception as e:
                        logging.warning(f"Failed to log/set tag for retried keyword: {e}")
                    self.retry_success = False
                    self.failed_keyword = None
                    return

        # 🔄 Refresh variable view if execution is active
        if self.gui_controller and getattr(self.gui_controller, "gui_ready", False):
            try:
                if self.continue_event.is_set():  # Only refresh if not paused
                    self.gui_controller.schedule_variable_refresh()
            except Exception as e:
                logging.warning(f"Variable refresh failed: {e}")

        # 🧹 Pop keyword from stack
        if self.keyword_stack:
            self.keyword_stack.pop()

    # ✅ Add helper for safe async waiting
    def _wait_for_user_action(self):
        if self.continue_event.is_set():
            # Handle retry or skip after GUI interaction
            if self.skip_keyword:
                self._mark_keyword_skipped()
            elif self.retry_success:
                self._mark_keyword_retried()
            return
        if self.gui_controller:
            self.gui_controller.root.after(100, self._wait_for_user_action)

    def _mark_keyword_skipped(self):
        try:
            self.builtin.log(f"[Debugger] Skipped keyword: {self.failed_keyword.name}", "WARN")
            self.builtin.set_tags("debugger-skipped")
        except Exception as e:
            logging.warning(f"Failed to log/set tag for skipped keyword: {e}")
        self.skip_keyword = False
        self.failed_keyword = None

    def _mark_keyword_retried(self):
        try:
            self.builtin.log(f"[Debugger] Retried keyword succeeded: {self.failed_keyword.name}", "INFO")
            self.builtin.set_tags("debugger-retried")
        except Exception as e:
            logging.warning(f"Failed to log/set tag for retried keyword: {e}")
        self.retry_success = False
        self.failed_keyword = None

    def retry_keyword(self, kw_name, args):
        try:
            result = self.builtin.run_keyword_and_ignore_error(kw_name, *args)
            logging.info(f"Retry result for {kw_name}: {result}")
            return result
        except Exception as e:
            logging.exception("Exception during retry:")
            return ('FAIL', str(e))

    def parse_arg(self, val):
        if not isinstance(val, str):
            return val

        val = val.strip()
        if not val:
            return val

        lowered = val.lower()
        if lowered in ('none', 'null'):
            return None
        if lowered == 'true':
            return True
        if lowered == 'false':
            return False

        try:
            return ast.literal_eval(val)
        except:
            return val

    def _normalize_keyword_name(self, raw_name):
        return raw_name.strip().split("  ")[0].strip().lower()
