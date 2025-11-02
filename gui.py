import tkinter as tk
from tkinter import scrolledtext, messagebox, ttk
from datetime import datetime
import threading
from functools import wraps
from robot.libdocpkg import LibraryDocumentation
import logging
from robot.libraries.BuiltIn import BuiltIn
import os
from .event_logger import (
    log_suite_start,
    log_suite_end,
    log_test_start,
    log_test_end,
)




class SimpleRetryGUI:
    # Class constants
    MAX_LOG_LINES = 1000
    MAX_FAILURE_LOG_LINES = 500
    VARIABLE_REFRESH_DELAY_MS = 1000  # Increased from 300ms for VDI performance
    # DEBUGGER_VERSION = "1.5.1"
    
    def __init__(self, core):
        self.core = core
        self.gui_ready = False
        core.gui_controller = self
        self._lock = threading.Lock()
        self.execution_in_progress = False
        self._current_call_stack = None  # Store current call stack for viewing
        self._var_refresh_id = None  # Track variable refresh timer

        self.root = tk.Tk()
        self.root.title(f"Robot Framework Debugger")
        self.root.geometry("900x700")
        self.root.minsize(850, 600)
        self.root.protocol("WM_DELETE_WINDOW", self._on_window_close)
        self.root.bind('<Control-q>', self._emergency_exit)  # Emergency exit shortcut
        self.root.columnconfigure(0, weight=1)
        self.root.rowconfigure(1, weight=1)  # Failure log should expand
        self.root.rowconfigure(2, weight=2)  # Tabs should expand more
        self.max_log_lines = self.MAX_LOG_LINES


        self.libraries = {}
        self.library_names = []
        self._pending_libraries = []
        self._libraries_loaded = False  # Track if libraries have been loaded for lazy-loading
        self._setup_ui()
        self.gui_ready = True
        
        # Process any libraries that were imported before GUI was ready
        if self._pending_libraries:
            for libname in self._pending_libraries:
                logging.info(f"[Debugger GUI] Processing pending library: {libname}")
                self.library_imported(libname)
            self._pending_libraries.clear()

    def _setup_ui(self):
        # === TEST CONTROL BAR ===
        control_bar = tk.Frame(self.root, bg="#d0e8ff", relief=tk.RIDGE, borderwidth=1)
        control_bar.grid(row=0, column=0, sticky="ew", padx=10, pady=3)
        
        # Left side: Status label
        self.control_status_label = tk.Label(
            control_bar,
            text="[READY]",
            font=("Segoe UI", 9, "bold"),
            bg="#d0e8ff",
            fg="#003366",
            anchor='w',
            padx=10
        )
        self.control_status_label.pack(side=tk.LEFT, fill=tk.X, expand=True)
        
        # Right side: Start button
        self.start_test_btn = tk.Button(
            control_bar,
            text="[>] Start",
            command=self._on_start_test,
            bg="#4CAF50",
            fg="white",
            font=("Segoe UI", 9, "bold"),
            padx=15,
            pady=3,
            cursor="hand2"
        )
        self.start_test_btn.pack(side=tk.RIGHT, padx=8, pady=3)
        
        # View Call Stack button (hidden by default)
        self.view_stack_btn = tk.Button(
            control_bar,
            text="[STACK] View",
            command=self._show_call_stack_window,
            bg="#4A90E2",
            fg="white",
            font=("Segoe UI", 9),
            padx=10,
            pady=3,
            cursor="hand2"
        )
        # Initially hidden, will show when there's a failure with stack
        # self.view_stack_btn.pack(side=tk.RIGHT, padx=3, pady=3)

        # === Failure Info Panel ===
        self.failure_text = scrolledtext.ScrolledText(
            self.root,
            wrap=tk.WORD,
            height=20,
            bg="#1e1e1e",
            fg="#e0e0e0",
            insertbackground="white",
            font=("Consolas", 10),
            borderwidth=1,
            relief=tk.FLAT
        )
        
        # Enhanced tag configurations for better visual hierarchy
        # Status tags
        self.failure_text.tag_config("fail", foreground="#ff6b6b", font=("Consolas", 10, "bold"))
        self.failure_text.tag_config("pass", foreground="#51cf66", font=("Consolas", 10, "bold"))
        self.failure_text.tag_config("pending", foreground="#868e96", font=("Consolas", 10, "italic"))
        self.failure_text.tag_config("warning", foreground="#ffd43b", font=("Consolas", 10, "bold"))
        
        # Component tags with icons
        self.failure_text.tag_config("header", foreground="#74c0fc", font=("Consolas", 11, "bold"))
        self.failure_text.tag_config("timestamp", foreground="#868e96", font=("Consolas", 9))
        self.failure_text.tag_config("keyword", foreground="#ffd43b", font=("Consolas", 10, "bold"))
        self.failure_text.tag_config("library", foreground="#da77f2", font=("Consolas", 10))
        self.failure_text.tag_config("args", foreground="#8ce99a", font=("Consolas", 9))
        self.failure_text.tag_config("message", foreground="#ff8787", font=("Consolas", 10))
        self.failure_text.tag_config("stack", foreground="#ffa94d", font=("Consolas", 9))
        
        # Separator tags
        self.failure_text.tag_config("separator", foreground="#495057")
        self.failure_text.tag_config("section", foreground="#339af0", font=("Consolas", 10, "bold"))
        
        # Labels and values
        self.failure_text.tag_config("label", foreground="#74c0fc", font=("Consolas", 10))
        self.failure_text.tag_config("value", foreground="#e0e0e0", font=("Consolas", 10))
        
        self.failure_text.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
        self.failure_text.config(state=tk.DISABLED)

        # Use improved tab style
        style = ttk.Style()
        style.theme_use("clam")  # Better rendering than default

        style.configure("TNotebook", background="#dcdcdc", borderwidth=1)
        style.configure("TNotebook.Tab", background="#f2f2f2", padding=(12, 6), font=("Segoe UI", 10))
        style.map("TNotebook.Tab", background=[("selected", "#ffffff")])

        exit_btn = tk.Button(
            self.root,
            text="[X] Close Debugger",
            command=self.safe_close,
            bg="#f44336",
            fg="white"
        )
        exit_btn.grid(row=99, column=0, pady=10)

        # === Sub-tabs for Retry, Custom Keyword, Variable Inspector, and Execution Trace ===
        self.sub_tabs = ttk.Notebook(self.root)
        self.sub_tabs.grid(row=2, column=0, sticky="nsew", padx=10, pady=10)

        # Create sub-tabs frames
        self.retry_tab = tk.Frame(self.sub_tabs)
        self.custom_tab = tk.Frame(self.sub_tabs)
        self.var_tab = tk.Frame(self.sub_tabs)

        self.sub_tabs.add(self.retry_tab, text="Retry Failed Keyword")
        self.sub_tabs.add(self.custom_tab, text="Run Custom Keyword")
        self.sub_tabs.add(self.var_tab, text="Variable Inspector")

        self.sub_tabs.bind("<<NotebookTabChanged>>", self._on_tab_changed)

        self._setup_variable_tab()
        self._setup_retry_tab()
        self._setup_custom_tab()

    def _on_tab_changed(self, event):
        selected_tab = event.widget.tab(event.widget.select(), "text")
        if selected_tab == "Variable Inspector":
            if self._has_active_execution_context():
                self._start_variable_refresh()
            else:
                self._refresh_variable_view()  # Show "not active" message
        else:
            self._stop_variable_refresh()  # Stop refresh when tab not visible
            
        if selected_tab == "Run Custom Keyword":
            # Lazy-load libraries when custom tab is first accessed
            if not self._libraries_loaded:
                self._libraries_loaded = True
                self._refresh_library_dropdown()
                logging.info("[Debugger GUI] Lazy-loading libraries for custom keyword tab")

    def _start_variable_refresh(self):
        """Start periodic variable refresh"""
        # Cancel any existing refresh timer
        self._stop_variable_refresh()
        
        # Do initial refresh
        self._refresh_variable_view()
        
        # Schedule next refresh
        if self._has_active_execution_context():
            self._var_refresh_id = self.root.after(
                self.VARIABLE_REFRESH_DELAY_MS, 
                self._start_variable_refresh
            )
    
    def _stop_variable_refresh(self):
        """Stop periodic variable refresh"""
        if self._var_refresh_id is not None:
            try:
                self.root.after_cancel(self._var_refresh_id)
            except:
                pass  # Timer already cancelled or doesn't exist
            self._var_refresh_id = None

    # === RETRY TAB ===
    def _setup_retry_tab(self):
        self.kw_name_var = tk.StringVar()

        kw_frame = tk.Frame(self.retry_tab)
        kw_frame.pack(fill=tk.X, padx=5, pady=5)

        tk.Label(kw_frame, text="Keyword Name:").pack(side=tk.LEFT)
        self.kw_name_entry = tk.Entry(kw_frame, textvariable=self.kw_name_var, width=50)
        self.kw_name_entry.pack(side=tk.LEFT, padx=5)

        self.args_frame = tk.LabelFrame(self.retry_tab, text="Edit Keyword Arguments", padx=5, pady=5)
        self.args_frame.pack(fill=tk.X, padx=5, pady=5)

        buttons_frame = tk.Frame(self.retry_tab)
        buttons_frame.pack(fill=tk.X, padx=5, pady=5)

        self.retry_btn = tk.Button(buttons_frame, text="Retry and Continue", command=self._on_retry_and_continue)
        self.retry_btn.pack(side=tk.LEFT, padx=5)


        self.add_arg_btn = tk.Button(buttons_frame, text="+ Add Arg", command=self._on_add_argument)
        self.add_arg_btn.pack(side=tk.LEFT, padx=5)

        self.skip_kw_btn = tk.Button(buttons_frame, text="Skip and Continue", command=self._on_skip_keyword, bg="#DAA520")
        self.skip_kw_btn.pack(side=tk.LEFT, padx=5)

        self.skip_btn = tk.Button(buttons_frame, text="Skip Test", command=self._on_skip_test, bg="#FFA500")
        self.skip_btn.pack(side=tk.LEFT, padx=5)

        self.abort_btn = tk.Button(buttons_frame, text="Abort Suite", command=self._on_abort_suite, bg="#FF6347")
        self.abort_btn.pack(side=tk.RIGHT, padx=5)

        # === IGNORE KEYWORDS SECTION ===
        ignore_frame = tk.LabelFrame(self.retry_tab, text="[IGNORE] Keywords", padx=8, pady=5)
        ignore_frame.pack(fill=tk.X, padx=5, pady=5)
        
        # Info note
        info_label = tk.Label(
            ignore_frame,
            text="[i] Click 'Refresh' button to load keywords from test libraries before adding to ignore list",
            font=("Segoe UI", 8, "italic"),
            fg="#666666",
            anchor='w'
        )
        info_label.pack(fill=tk.X, pady=(0, 3))
        
        # Top row: Search + Dropdown + Add button
        top_row = tk.Frame(ignore_frame)
        top_row.pack(fill=tk.X, pady=(0, 3))
        
        tk.Label(top_row, text="Search:", font=("Segoe UI", 9)).pack(side=tk.LEFT, padx=(0, 3))
        
        self.ignore_search_var = tk.StringVar()
        self.ignore_search_var.trace('w', lambda *args: self._filter_ignore_dropdown())
        search_entry = tk.Entry(top_row, textvariable=self.ignore_search_var, width=25, font=("Consolas", 9))
        search_entry.pack(side=tk.LEFT, padx=3)
        
        self.ignore_keyword_dropdown = ttk.Combobox(
            top_row,
            state="readonly",
            width=35,
            font=("Consolas", 9)
        )
        self.ignore_keyword_dropdown.pack(side=tk.LEFT, padx=3)
        
        tk.Button(
            top_row,
            text="[+] Add to Ignore List",
            command=self._add_keyword_to_ignore,
            bg="#4CAF50",
            fg="white",
            font=("Segoe UI", 9),
            padx=10,
            pady=2
        ).pack(side=tk.LEFT, padx=2)
        
        tk.Button(
            top_row,
            text="[R] Refresh",
            command=self._refresh_ignore_keyword_list,
            font=("Segoe UI", 9),
            padx=10,
            pady=2
        ).pack(side=tk.LEFT, padx=2)
        
        # Bottom row: Compact ignored list display
        bottom_row = tk.Frame(ignore_frame)
        bottom_row.pack(fill=tk.X, pady=(3, 0))
        
        tk.Label(bottom_row, text="Ignored:", font=("Segoe UI", 9, "bold")).pack(side=tk.LEFT, padx=(0, 5))
        
        # Scrollable text widget for compact display
        self.ignored_display_text = tk.Text(
            bottom_row,
            height=2,
            wrap=tk.WORD,
            font=("Consolas", 8),
            bg="#fff9e6",
            relief=tk.SUNKEN,
            borderwidth=1
        )
        self.ignored_display_text.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=3)
        self.ignored_display_text.config(state=tk.DISABLED)
        
        # Action buttons
        btn_frame = tk.Frame(bottom_row)
        btn_frame.pack(side=tk.LEFT, padx=3)
        
        tk.Button(
            btn_frame,
            text="[-] Remove",
            command=self._remove_keyword_from_ignore,
            bg="#f44336",
            fg="white",
            font=("Segoe UI", 9),
            padx=8,
            pady=2
        ).pack(side=tk.TOP, pady=1)
        
        tk.Button(
            btn_frame,
            text="[CLEAR] All",
            command=self._clear_all_ignores,
            bg="#FFE4E1",
            font=("Segoe UI", 9),
            padx=8,
            pady=2
        ).pack(side=tk.TOP, pady=1)
        
        self._all_keywords = []  # Store all available keywords
        self._refresh_ignore_keyword_list()

    # === CUSTOM EXECUTOR TAB ===
    def _setup_custom_tab(self):
        self.library_var = tk.StringVar()
        self.keyword_var = tk.StringVar()
        self.command_var = tk.StringVar()
        self.custom_search_var = tk.StringVar()
        # self.result_var = tk.StringVar()

        selector_frame = tk.Frame(self.custom_tab)
        selector_frame.pack(fill=tk.X, padx=10, pady=5)

        tk.Label(selector_frame, text="Library:").pack(side=tk.LEFT)
        self.library_dropdown = ttk.Combobox(selector_frame, textvariable=self.library_var, state="readonly")
        self.library_dropdown.pack(side=tk.LEFT, padx=5)
        
        # Library status indicator
        self.library_status_var = tk.StringVar(value="")
        self.library_status_label = tk.Label(
            selector_frame,
            textvariable=self.library_status_var,
            font=("Segoe UI", 8, "italic"),
            fg="#666666"
        )
        self.library_status_label.pack(side=tk.LEFT, padx=10)

        # Add search box for keyword filtering
        search_frame = tk.Frame(self.custom_tab)
        search_frame.pack(fill=tk.X, padx=10, pady=5)
        
        tk.Label(search_frame, text="[SEARCH] Keyword:").pack(side=tk.LEFT)
        self.custom_search_var.trace('w', lambda *args: self._filter_custom_keywords())
        search_entry = tk.Entry(search_frame, textvariable=self.custom_search_var, width=40, font=("Consolas", 9))
        search_entry.pack(side=tk.LEFT, padx=5)
        
        tk.Button(
            search_frame,
            text="[X] Clear",
            command=lambda: self.custom_search_var.set(""),
            font=("Segoe UI", 8),
            padx=5
        ).pack(side=tk.LEFT)

        keyword_frame = tk.Frame(self.custom_tab)
        keyword_frame.pack(fill=tk.X, padx=10, pady=5)
        
        tk.Label(keyword_frame, text="Keyword:").pack(side=tk.LEFT)
        self.keyword_dropdown = ttk.Combobox(keyword_frame, width=50, textvariable=self.keyword_var, state="readonly")
        self.keyword_dropdown.pack(side=tk.LEFT, padx=5)

        self.library_dropdown.bind("<<ComboboxSelected>>", self._on_library_selected)
        self.keyword_dropdown.bind("<<ComboboxSelected>>", self._on_keyword_selected)

        self.custom_args_frame = tk.LabelFrame(self.custom_tab, text="Keyword Arguments")
        self.custom_args_frame.pack(fill=tk.X, padx=10, pady=5)

        btn_frame = tk.Frame(self.custom_tab)
        btn_frame.pack(fill=tk.X, padx=10, pady=5)

        tk.Button(btn_frame, text="Execute", command=self._execute_command).pack(side=tk.LEFT)
        # self.result_display = tk.Label(btn_frame, textvariable=self.result_var, fg='green')

        # self.result_display.pack(side=tk.LEFT, padx=10)
        tk.Button(btn_frame, text="+ Add Arg", command=self._add_custom_argument_field).pack(side=tk.LEFT, padx=5)

        doc_frame = tk.LabelFrame(self.custom_tab, text="Keyword Documentation", padx=5, pady=5)
        doc_frame.pack(fill=tk.BOTH, expand=True, padx=10, pady=5)

        self.doc_display = scrolledtext.ScrolledText(doc_frame, wrap=tk.WORD)
        self.doc_display.pack(fill=tk.BOTH, expand=True)
        self.doc_display.config(state=tk.DISABLED)
        self.executor_ready = True
        # Note: Libraries are loaded lazily when user first accesses the custom tab
        # This improves startup performance in VDI environments

    def _on_library_selected(self, event=None):
        lib = self.library_var.get()
        if lib not in self.libraries:
            return
        self.keyword_dropdown['values'] = [kw['name'] for kw in self.libraries[lib]]
        if self.keyword_dropdown['values']:
            self.keyword_var.set(self.keyword_dropdown['values'][0])
            self._on_keyword_selected()
    
    def _filter_custom_keywords(self):
        """Filter keyword dropdown based on search text"""
        lib = self.library_var.get()
        if not lib or lib not in self.libraries:
            return
        
        search_text = self.custom_search_var.get().lower()
        all_keywords = [kw['name'] for kw in self.libraries[lib]]
        
        if search_text:
            filtered = [kw for kw in all_keywords if search_text in kw.lower()]
        else:
            filtered = all_keywords
        
        self.keyword_dropdown['values'] = filtered
        
        # Auto-select first match if available
        if filtered:
            current = self.keyword_var.get()
            if current not in filtered:
                self.keyword_var.set(filtered[0])
                self._on_keyword_selected()
        else:
            self.keyword_var.set("")

    def _on_keyword_selected(self, event=None):
        lib = self.library_var.get()
        kw_name = self.keyword_var.get()

        if not lib or not kw_name:
            return

        if lib in self.libraries:
            for kw in self.libraries[lib]:
                if kw['name'] == kw_name:
                    self._populate_custom_args_editor(kw['args'])

                    # ✅ Show signature
                    args_text = ", ".join(
                        a.name if hasattr(a, "name") else str(a)
                        for a in kw['args']
                    )
                    signature = f"{kw_name}({args_text})"
                    self.command_var.set(signature)

                    # ✅ Show doc
                    self.doc_display.config(state=tk.NORMAL)
                    self.doc_display.delete("1.0", tk.END)
                    self.doc_display.insert(tk.END, f"{kw_name}\n\nSignature:\n{signature}\n\nDoc:\n{kw.get('doc', '')}")
                    self.doc_display.config(state=tk.DISABLED)
                    break

    def _populate_custom_args_editor(self, args):
        for widget in self.custom_args_frame.winfo_children():
            widget.destroy()
        self.custom_arg_vars = []

        for i, arg in enumerate(args or []):
            if hasattr(arg, "name"):
                name = arg.name
                default = getattr(arg, "default", None)
            else:
                name = str(arg)
                default = None

            label = f"{name}" if default is None else f"{name} (default={default})"
            var = tk.StringVar(value=str(default) if default is not None else "")

            frame = tk.Frame(self.custom_args_frame)
            frame.pack(anchor='w', pady=2, fill='x')

            tk.Label(frame, text=f"{label}:").pack(side='left')
            entry = tk.Entry(frame, textvariable=var, width=60)
            entry.pack(side='left', padx=5)
            tk.Button(frame, text="–", command=lambda f=frame: self._remove_custom_argument_field(f)).pack(side='left')

            # Optional tooltip for extra polish
            def create_tooltip(widget, text):
                tip = None

                def on_enter(event):
                    nonlocal tip
                    tip = tk.Toplevel(widget)
                    tip.wm_overrideredirect(True)
                    x = widget.winfo_rootx() + 20
                    y = widget.winfo_rooty() + 20
                    tip.geometry(f"+{x}+{y}")
                    tk.Label(tip, text=text, background="lightyellow", relief='solid', borderwidth=1).pack()

                def on_leave(event):
                    nonlocal tip
                    if tip:
                        tip.destroy()

                widget.bind("<Enter>", on_enter)
                widget.bind("<Leave>", on_leave)

            create_tooltip(entry, label)
            self.custom_arg_vars.append(var)

        # self._add_custom_argument_field()  # start with one empty field

    def _add_custom_argument_field(self, value=""):
        var = tk.StringVar(value=str(value))
        frame = tk.Frame(self.custom_args_frame)
        frame.pack(anchor='w', pady=2, fill='x')
        tk.Label(frame, text=f"Arg {len(self.custom_arg_vars) + 1}:").pack(side='left')
        tk.Entry(frame, textvariable=var, width=60).pack(side='left', padx=2)
        tk.Button(frame, text="–", command=lambda f=frame: self._remove_custom_argument_field(f)).pack(side='left')
        self.custom_arg_vars.append(var)

    def _remove_custom_argument_field(self, frame):
        idx = list(self.custom_args_frame.children.values()).index(frame)
        frame.destroy()
        del self.custom_arg_vars[idx]


    def _update_keywords(self):
        lib = self.library_var.get()
        menu = self.keyword_dropdown["menu"]
        menu.delete(0, "end")

        if lib not in self.libraries:
            return

        keywords = self.libraries[lib]
        for kw in keywords:
            menu.add_command(label=kw['name'], command=lambda name=kw['name']: self.keyword_var.set(name))

    def _update_command_from_keyword(self):
        lib = self.library_var.get()
        kw_name = self.keyword_var.get()
        if lib in self.libraries:
            for kw in self.libraries[lib]:
                if kw['name'] == kw_name:
                    args = [arg for arg in kw['args'] if '=' not in arg]
                    self.command_var.set(f"{lib}.{kw_name}    {'    '.join(args)}")

                    self.doc_display.config(state=tk.NORMAL)
                    self.doc_display.delete("1.0", tk.END)
                    self.doc_display.insert(tk.END, f"{kw_name}\n\nArgs:\n{kw['args']}\n\nDoc:\n{kw['doc']}")
                    self.doc_display.config(state=tk.DISABLED)
                    break

    def _execute_command(self):
        if self.execution_in_progress:
            self._update_failure_display(
                "Execution in progress. Please wait.",
                "[Custom] Busy",
                "fail"
            )
            return

        lib = self.library_var.get()
        kw = self.keyword_var.get()
        if not lib or not kw:
            self._update_failure_display(
                "Cannot execute. Please select both library and keyword.",
                "[Custom] Execution Blocked",
                "fail"
            )
            return

        args = [self.core.parse_arg(var.get()) for var in getattr(self, 'custom_arg_vars', [])]
        self.execution_in_progress = True

        def _run():
            try:
                result = BuiltIn().run_keyword(f"{lib}.{kw}", *args)
                BuiltIn().set_test_variable("${RETURN_VALUE}", result)
                self._update_failure_display(
                    f"Executed: {lib}.{kw}\nArgs: {args}\n\n${{RETURN_VALUE}} = {result}",
                    f"[Custom] {lib}.{kw} [OK]",
                    "pass"
                )
            except Exception as e:
                self._update_failure_display(
                    f"Executed: {lib}.{kw}\nArgs: {args}\n\nError: {e}",
                    f"[Custom] {lib}.{kw} [FAIL]",
                    "fail"
                )
            finally:
                self.execution_in_progress = False

        threading.Thread(target=_run, daemon=True).start()

    def show_failure(self, suite, test, keyword, message, args, call_stack=None):
        timestamp = datetime.now().strftime("%H:%M:%S")

        # Store call stack optimized - only keep necessary data, not full objects
        if call_stack:
            self._current_call_stack = [
                {
                    'name': getattr(kw, 'name', 'UNKNOWN'),
                    'args': list(getattr(kw, 'args', []))[:10]  # Limit args to first 10
                }
                for kw in call_stack[:30]  # Limit stack depth to 30 levels
            ]
        else:
            self._current_call_stack = None
        
        # Show/hide stack button based on availability
        if call_stack:
            self.view_stack_btn.pack(side=tk.RIGHT, padx=3, pady=3)
        else:
            self.view_stack_btn.pack_forget()
        
        # Enhanced failure display
        self.failure_text.config(state=tk.NORMAL)
        
        # Header with visual separator
        self.failure_text.insert(tk.END, f"\n{'═' * 70}\n", "separator")
        self.failure_text.insert(tk.END, f"[!] TEST FAILURE DETECTED\n", "fail")
        self.failure_text.insert(tk.END, f"[TIME] {timestamp}\n", "timestamp")
        self.failure_text.insert(tk.END, f"{'═' * 70}\n", "separator")
        
        # Test details with labels
        self.failure_text.insert(tk.END, "\n[TEST] ", "label")
        self.failure_text.insert(tk.END, f"{test}\n", "value")
        
        self.failure_text.insert(tk.END, "[KEYWORD] ", "label")
        self.failure_text.insert(tk.END, f"{keyword}\n", "keyword")
        
        # Message
        self.failure_text.insert(tk.END, "\n[ERROR]:\n", "label")
        for line in message.strip().split('\n'):
            if line.strip():
                self.failure_text.insert(tk.END, f"   {line}\n", "message")
        
        # Footer separator
        self.failure_text.insert(tk.END, f"\n{'═' * 70}\n", "separator")

        self.failure_text.see(tk.END)
        self.failure_text.config(state=tk.DISABLED)

        # Set keyword for retry tab
        self.kw_name_var.set(keyword)

        # Resolve arguments for retry
        builtin = BuiltIn()
        resolved_args = []
        for a in args:
            if isinstance(a, str) and a.startswith("${") and a.endswith("}"):
                try:
                    resolved_args.append(builtin.get_variable_value(a))
                except:
                    resolved_args.append(a)
            else:
                resolved_args.append(a)

        self._build_args_editor(resolved_args)
        self._show_window()
        if hasattr(self, "retry_btn"):
            self.retry_btn.config(state=tk.NORMAL)
        if hasattr(self, "skip_kw_btn"):
            self.skip_kw_btn.config(state=tk.NORMAL)

        self.update_status("Ready for action.", "blue")

    def _build_args_editor(self, args):
        for widget in self.args_frame.winfo_children():
            widget.destroy()
        self.arg_vars = []
        for val in args or []:
            self._add_argument_field(val)

    def _add_argument_field(self, value=""):
        index = len(self.arg_vars)
        var = tk.StringVar(value=str(value))
        frame = tk.Frame(self.args_frame)
        frame.pack(anchor='w', pady=2, fill='x')
        tk.Label(frame, text=f"Arg {index + 1}:").pack(side='left')
        tk.Entry(frame, textvariable=var, width=70).pack(side='left', padx=2)
        tk.Button(frame, text="–", command=lambda f=frame: self._remove_argument_field(f)).pack(side='left')
        self.arg_vars.append(var)

    def _remove_argument_field(self, frame):
        idx = list(self.args_frame.children.values()).index(frame)
        frame.destroy()
        del self.arg_vars[idx]

    def _on_add_argument(self):
        self._add_argument_field()

    # === TEST CONTROL HANDLERS ===
    def _on_start_test(self):
        """Handle Start Test button click."""
        self.start_test_btn.config(state=tk.DISABLED)
        self.control_status_label.config(text="[>] Starting...", fg="#006600")
        
        # Enhanced log message
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.failure_text.config(state=tk.NORMAL)
        self.failure_text.insert(tk.END, f"\n{'─' * 70}\n", "separator")
        self.failure_text.insert(tk.END, "[>] TEST EXECUTION STARTED", "pass")
        self.failure_text.insert(tk.END, f" [{timestamp}]\n", "timestamp")
        self.failure_text.insert(tk.END, f"   User initiated test run\n", "value")
        self.failure_text.insert(tk.END, f"{'─' * 70}\n\n", "separator")
        self.failure_text.see(tk.END)
        self.failure_text.config(state=tk.DISABLED)
        
        # Unblock the test suite
        self.core.test_start_event.set()
        
        logging.info("[Debugger GUI] User clicked Start Test")
    
    def show_ready_state(self, suite_name):
        """Show that test is ready but not started."""
        self.control_status_label.config(
            text=f"[READY] {suite_name}",
            fg="#003366"
        )
        self.start_test_btn.config(state=tk.NORMAL)
        
        # Show message in log
        self.failure_text.config(state=tk.NORMAL)
        self.failure_text.insert(
            tk.END,
            f"\n{'='*60}\n"
            f"Test Suite Ready: {suite_name}\n"
            f"Click '[>] Start' button to begin\n"
            f"Configure ignore keywords in Retry tab before starting\n"
            f"{'='*60}\n\n",
            "header"
        )
        self.failure_text.see(tk.END)
        self.failure_text.config(state=tk.DISABLED)
        
        # Bring window to front
        self._show_window()
    
    def show_running_state(self):
        """Update UI when test starts running."""
        self.control_status_label.config(
            text="[>] Running...",
            fg="#006600"
        )
        self.start_test_btn.config(state=tk.DISABLED, text="[>] Running...")

    # === IGNORE KEYWORDS HANDLERS ===
    def _refresh_ignore_keyword_list(self):
        """Populate the keyword dropdown from libraries and seen keywords."""
        # Gather all keywords
        all_keywords = set()
        
        # From loaded libraries
        for lib, keywords in self.libraries.items():
            for kw in keywords:
                all_keywords.add(kw['name'])
        
        # From seen keywords during execution
        if hasattr(self.core, 'seen_keywords'):
            all_keywords.update(self.core.seen_keywords)
        
        self._all_keywords = sorted(list(all_keywords))
        
        # Update dropdown with filter
        self._filter_ignore_dropdown()
        
        # Update ignored list display
        self._update_ignored_display()
    
    def _filter_ignore_dropdown(self):
        """Filter dropdown based on search term."""
        search_term = self.ignore_search_var.get().lower()
        
        if search_term:
            filtered = [kw for kw in self._all_keywords if search_term in kw.lower()]
        else:
            filtered = self._all_keywords
        
        self.ignore_keyword_dropdown['values'] = filtered
        if filtered:
            self.ignore_keyword_dropdown.current(0)
    
    def _add_keyword_to_ignore(self):
        """Add selected keyword from dropdown to ignore list."""
        selected = self.ignore_keyword_dropdown.get()
        if not selected:
            messagebox.showwarning("No Selection", "Please select a keyword from the dropdown.")
            return
        
        if selected in self.core.ignored_keywords:
            messagebox.showinfo("Already Ignored", f"'{selected}' is already in the ignore list.")
            return
        
        self.core.ignored_keywords.add(selected)
        self._update_ignored_display()
        
        # Enhanced log message
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.failure_text.config(state=tk.NORMAL)
        self.failure_text.insert(tk.END, f"\n{'─' * 70}\n", "separator")
        self.failure_text.insert(tk.END, "[+] KEYWORD IGNORED", "pass")
        self.failure_text.insert(tk.END, f" [{timestamp}]\n", "timestamp")
        self.failure_text.insert(tk.END, f"   Added '", "value")
        self.failure_text.insert(tk.END, f"{selected}", "keyword")
        self.failure_text.insert(tk.END, f"' to ignore list\n", "value")
        self.failure_text.insert(tk.END, f"{'─' * 70}\n", "separator")
        self.failure_text.see(tk.END)
        self.failure_text.config(state=tk.DISABLED)
    
    def _remove_keyword_from_ignore(self):
        """Remove keyword from ignored list (asks user to type or select)."""
        if not self.core.ignored_keywords:
            messagebox.showinfo("No Keywords", "No keywords in ignore list.")
            return
        
        # Show dialog with list of ignored keywords
        from tkinter import simpledialog
        ignored_list = sorted(self.core.ignored_keywords)
        choices = "\n".join(f"{i+1}. {kw}" for i, kw in enumerate(ignored_list))
        
        keyword = simpledialog.askstring(
            "Remove Keyword",
            f"Enter keyword name or number:\n\n{choices}",
            parent=self.root
        )
        
        if not keyword:
            return
        
        # Check if it's a number
        try:
            idx = int(keyword) - 1
            if 0 <= idx < len(ignored_list):
                keyword = ignored_list[idx]
        except ValueError:
            pass
        
        if keyword in self.core.ignored_keywords:
            self.core.ignored_keywords.remove(keyword)
            self._update_ignored_display()
            
            # Enhanced log message
            timestamp = datetime.now().strftime("%H:%M:%S")
            self.failure_text.config(state=tk.NORMAL)
            self.failure_text.insert(tk.END, f"\n{'─' * 70}\n", "separator")
            self.failure_text.insert(tk.END, "[-] KEYWORD REMOVED FROM IGNORE LIST", "warning")
            self.failure_text.insert(tk.END, f" [{timestamp}]\n", "timestamp")
            self.failure_text.insert(tk.END, f"   Removed '", "value")
            self.failure_text.insert(tk.END, f"{keyword}", "keyword")
            self.failure_text.insert(tk.END, f"' from ignore list\n", "value")
            self.failure_text.insert(tk.END, f"{'─' * 70}\n", "separator")
            self.failure_text.see(tk.END)
            self.failure_text.config(state=tk.DISABLED)
        else:
            messagebox.showwarning("Not Found", f"'{keyword}' not in ignore list.")
    
    def _clear_all_ignores(self):
        """Clear all ignored keywords."""
        if not self.core.ignored_keywords:
            return
        
        count = len(self.core.ignored_keywords)
        self.core.ignored_keywords.clear()
        self._update_ignored_display()
        
        # Enhanced log message
        timestamp = datetime.now().strftime("%H:%M:%S")
        self.failure_text.config(state=tk.NORMAL)
        self.failure_text.insert(tk.END, f"\n{'─' * 70}\n", "separator")
        self.failure_text.insert(tk.END, "[X] IGNORE LIST CLEARED", "warning")
        self.failure_text.insert(tk.END, f" [{timestamp}]\n", "timestamp")
        self.failure_text.insert(tk.END, f"   Cleared {count} keyword(s) from ignore list\n", "value")
        self.failure_text.insert(tk.END, f"{'─' * 70}\n", "separator")
        self.failure_text.see(tk.END)
        self.failure_text.config(state=tk.DISABLED)
    
    def _update_ignored_display(self):
        """Update the compact text display of ignored keywords."""
        self.ignored_display_text.config(state=tk.NORMAL)
        self.ignored_display_text.delete(1.0, tk.END)
        
        if not self.core.ignored_keywords:
            self.ignored_display_text.insert(1.0, "(none)")
        else:
            # Display as comma-separated list
            ignored_str = ", ".join(sorted(self.core.ignored_keywords))
            self.ignored_display_text.insert(1.0, ignored_str)
        
        self.ignored_display_text.config(state=tk.DISABLED)
    
    def _on_add_argument(self):
        self._add_argument_field()

    def _on_retry_and_continue(self):
        # ✅ Debounce: Ignore if retry is already in progress
        if hasattr(self, "retry_btn") and str(self.retry_btn['state']) == 'disabled':
            return

        if not self.core.failed_keyword:
            messagebox.showerror("Error", "No failed keyword to retry.")
            return

        # Additional safety check
        if not hasattr(self, 'arg_vars') or not self.kw_name_var.get().strip():
            messagebox.showerror("Error", "Cannot retry - keyword information missing.")
            return

        # Disable buttons safely during retry
        if hasattr(self, "retry_btn"):
            self.retry_btn.config(state=tk.DISABLED)
        if hasattr(self, "skip_kw_btn"):
            self.skip_kw_btn.config(state=tk.DISABLED)

        kw_name = self.kw_name_var.get().strip()
        args = [self.core.parse_arg(var.get()) for var in self.arg_vars]

        self.update_status("Retrying keyword...", "blue")

        def run_retry():
            try:
                status, message = self.core.retry_keyword(kw_name, args)

                def after_retry():
                    if status == 'PASS':
                        # ✅ Retry succeeded → log it and continue
                        self.update_status("Retry succeeded. Continuing test...", "green")
                        self._update_failure_display(
                            f"Retry successful for keyword '{kw_name}'",
                            f"[{self.core.current_test}] Retry",
                            "pass"
                        )
                        self.core.retry_success = True
                        self.core.continue_event.set()  # ✅ Unblock Robot
                    else:
                        # ✅ Retry failed → log it and re-enable buttons
                        self.update_status("Retry failed. Try again or continue.", "red")
                        self._update_failure_display(
                            f"Retry failed for keyword '{kw_name}'\nReason: {message}",
                            f"[{self.core.current_test}] Retry",
                            "fail"
                        )

                        if hasattr(self, "retry_btn"):
                            self.retry_btn.config(state=tk.NORMAL)
                        if hasattr(self, "skip_kw_btn"):
                            self.skip_kw_btn.config(state=tk.NORMAL)

                self.root.after(0, after_retry)

            except Exception as e:
                def after_error():
                    self.update_status(f"Retry crashed: {e}", "red")
                    self._update_failure_display(
                        f"Retry crashed: {e}",
                        f"[{self.core.current_test}] Retry",
                        "fail"
                    )

                    if hasattr(self, "retry_btn"):
                        self.retry_btn.config(state=tk.NORMAL)
                    if hasattr(self, "skip_kw_btn"):
                        self.skip_kw_btn.config(state=tk.NORMAL)

                self.root.after(0, after_error)

        # ✅ Run retry in background thread so GUI stays responsive
        threading.Thread(target=run_retry, daemon=True).start()

    def _update_failure_display(self, text, prefix, status, keyword_name=None, args=None):
        """
        Update the failure display with enhanced visual formatting.
        Custom executor failures should not overwrite retry tab widgets.
        """
        # If there is no failed keyword and this is a custom log, log it and exit
        if not self.core.failed_keyword:
            if prefix.startswith("[Custom]"):
                self._log_custom_execution(text, status)
            return  # Stop here if no failed keyword exists

        timestamp = datetime.now().strftime("%H:%M:%S")
        test_name = self.core.current_test or "Unknown Test"
        if not keyword_name:
            keyword_name = self.core.failed_keyword.name if self.core.failed_keyword else "Unknown Keyword"
        if args is None:
            args = self.core.failed_keyword.args if self.core.failed_keyword else []

        self.failure_text.config(state=tk.NORMAL)
        
        # Status indicator and header
        status_icons = {"pass": "[OK]", "fail": "[FAIL]", "pending": "[WAIT]", "warning": "[WARN]"}
        icon = status_icons.get(status, "[INFO]")
        
        # Header with colored box
        header_text = f"\n{'═' * 70}\n"
        self.failure_text.insert(tk.END, header_text, "separator")
        
        status_text = f"{icon} {'PASSED' if status == 'pass' else 'FAILED' if status == 'fail' else status.upper()}"
        self.failure_text.insert(tk.END, f"{status_text}\n", status)
        
        # Timestamp
        self.failure_text.insert(tk.END, f"[TIME] {timestamp}\n", "timestamp")
        self.failure_text.insert(tk.END, f"{'─' * 70}\n", "separator")
        
        # Test name
        self.failure_text.insert(tk.END, "\n[TEST] ", "label")
        self.failure_text.insert(tk.END, f"{test_name}\n", "value")
        
        # Keyword name
        self.failure_text.insert(tk.END, "[KEYWORD] ", "label")
        self.failure_text.insert(tk.END, f"{keyword_name}\n", "keyword")
        
        # Arguments
        if args:
            self.failure_text.insert(tk.END, "[ARGS]:\n", "label")
            for i, arg in enumerate(args, 1):
                arg_str = str(arg)
                if len(arg_str) > 100:
                    arg_str = arg_str[:97] + "..."
                self.failure_text.insert(tk.END, f"   [{i}] ", "label")
                self.failure_text.insert(tk.END, f"{arg_str}\n", "args")
        
        # Reason/Message
        reason = text.strip()
        if reason:
            self.failure_text.insert(tk.END, "\n[MESSAGE]:\n", "label")
            # Handle multi-line messages
            for line in reason.split('\n'):
                if line.strip():
                    self.failure_text.insert(tk.END, f"   {line}\n", "message")
        
        # Return value if present
        if "${RETURN_VALUE}" in text or "return value" in text.lower():
            lines = text.splitlines()
            for line in lines:
                if "${RETURN_VALUE}" in line or "return value" in line.lower():
                    ret_val = line.split('=')[-1].strip()
                    self.failure_text.insert(tk.END, "\n[RETURN] ", "label")
                    self.failure_text.insert(tk.END, f"{ret_val}\n", "value")
        
        # Footer
        self.failure_text.insert(tk.END, f"{'═' * 70}\n", "separator")
        
        self.failure_text.see(tk.END)
        self.failure_text.config(state=tk.DISABLED)
        self._trim_failure_log()
    
    def _log_custom_execution(self, text, status):
        """Log custom keyword execution with enhanced formatting"""
        timestamp = datetime.now().strftime("%H:%M:%S")
        status_icons = {"pass": "[OK]", "fail": "[FAIL]", "pending": "[WAIT]", "warning": "[WARN]"}
        icon = status_icons.get(status, "[INFO]")
        
        self.failure_text.config(state=tk.NORMAL)
        
        self.failure_text.insert(tk.END, f"\n{'─' * 70}\n", "separator")
        self.failure_text.insert(tk.END, f"{icon} Custom Keyword Execution", "section")
        self.failure_text.insert(tk.END, f" [{timestamp}]\n", "timestamp")
        self.failure_text.insert(tk.END, f"   {text}\n", status)
        self.failure_text.insert(tk.END, f"{'─' * 70}\n", "separator")
        
        self.failure_text.see(tk.END)
        self.failure_text.config(tk.DISABLED)
        self._trim_failure_log()

    def _trim_failure_log(self, max_lines=None):
        if max_lines is None:
            max_lines = self.MAX_FAILURE_LOG_LINES
        lines = self.failure_text.get("1.0", tk.END).splitlines()
        if len(lines) > max_lines:
            trimmed = "\n".join(lines[-max_lines:])
            self.failure_text.config(state=tk.NORMAL)
            self.failure_text.delete("1.0", tk.END)
            self.failure_text.insert(tk.END, trimmed)
            self.failure_text.config(state=tk.DISABLED)

    # def update_status(self, text, color="black"):
    #     self.control_status_label.config(text=text, fg=color)
    def update_status(self, text, color="black"):
        # Now uses control_status_label (merged with control bar)
        self.control_status_label.config(text=text)
        # Keep control bar background consistent
        fg_color = {
            "blue": "#003366",
            "red": "#8B0000",
            "green": "#006600",
            "gray": "#666666",
            "orange": "#CC6600"
        }.get(color, "#003366")
        self.control_status_label.config(fg=fg_color)

    def _on_skip_test(self):
        self.update_status("[SKIP] Test skipped", "orange")
        self.core.skip_test = True
        self.core.continue_event.set()

    def _on_abort_suite(self):
        if messagebox.askyesno("Abort Suite", "Really abort entire test suite?"):
            self.update_status("[X] Suite aborted", "red")
            self.core.abort_suite = True
            self.core.continue_event.set()

    def _on_window_close(self):
        self.root.withdraw()

    def _show_window(self):
        # Only restore if window is withdrawn/minimized, preserving user's position
        if self.root.state() == 'withdrawn' or self.root.state() == 'iconic':
            self.root.deiconify()
        # Bring to front without forcing position reset
        self.root.lift()
        # Use focus() instead of focus_force() to avoid repositioning
        self.root.focus()

    def _show_call_stack_window(self):
        """Show call stack in a separate popup window."""
        if not hasattr(self, '_current_call_stack') or not self._current_call_stack:
            messagebox.showinfo("Call Stack", "No call stack available for this failure.")
            return
        
        # Create popup window
        stack_window = tk.Toplevel(self.root)
        stack_window.title("[STACK] Call Stack Trace")
        stack_window.geometry("700x500")
        stack_window.minsize(500, 300)  # Make it resizable with minimum size
        stack_window.transient(self.root)
        
        # Header
        header = tk.Label(
            stack_window,
            text="Call Stack Trace",
            font=("Segoe UI", 12, "bold"),
            bg="#4A90E2",
            fg="white",
            pady=10
        )
        header.pack(fill=tk.X)
        
        # Stack trace text widget
        stack_text = scrolledtext.ScrolledText(
            stack_window,
            wrap=tk.WORD,
            font=("Consolas", 10),
            bg="#1e1e1e",
            fg="white",
            padx=10,
            pady=10
        )
        stack_text.pack(fill=tk.BOTH, expand=True, padx=10, pady=10)
        
        # Configure color tags for different depth levels (visually appealing)
        colors = [
            "#00D4FF",  # Cyan - Level 0
            "#FFD700",  # Gold - Level 1
            "#FF6B6B",  # Coral - Level 2
            "#4ECDC4",  # Turquoise - Level 3
            "#95E1D3",  # Mint - Level 4
            "#C7CEEA",  # Lavender - Level 5
            "#FF9FF3",  # Pink - Level 6
            "#FFA500",  # Orange - Level 7
        ]
        
        for i, color in enumerate(colors):
            stack_text.tag_config(f"level{i}", foreground=color, font=("Consolas", 10, "bold"))
        
        # Header style
        stack_text.tag_config("header", foreground="#FFFFFF", font=("Consolas", 11, "bold"))
        stack_text.tag_config("arrow", foreground="#00FF00")
        
        # Insert header
        stack_text.insert(tk.END, "Execution Call Stack:\n", "header")
        stack_text.insert(tk.END, "=" * 70 + "\n\n", "header")
        
        # Build and insert stack trace with colors
        for depth, kw in enumerate(self._current_call_stack):
            indent = "  " * depth
            # Handle both dict format (new) and object format (legacy)
            if isinstance(kw, dict):
                kw_name = kw.get("name", "UNKNOWN")
                kw_args = kw.get("args", [])
            else:
                kw_name = getattr(kw, "name", "UNKNOWN")
                kw_args = getattr(kw, "args", [])
            
            # Format arguments
            args_preview = ""
            if kw_args:
                formatted_args = []
                for a in kw_args:
                    arg_str = str(a)
                    if len(arg_str) > 50:
                        arg_str = arg_str[:47] + "..."
                    formatted_args.append(arg_str)
                args_preview = ", ".join(formatted_args)
            
            # Color based on depth (cycle through colors)
            color_tag = f"level{depth % len(colors)}"
            
            # Insert with color
            stack_text.insert(tk.END, f"{indent}", color_tag)
            stack_text.insert(tk.END, "↳ ", "arrow")
            stack_text.insert(tk.END, f"{kw_name}", color_tag)
            stack_text.insert(tk.END, f"({args_preview})\n", color_tag)
        
        stack_text.config(state=tk.DISABLED)
        
        # Close button
        close_btn = tk.Button(
            stack_window,
            text="Close",
            command=stack_window.destroy,
            bg="#666666",
            fg="white",
            font=("Segoe UI", 10),
            padx=20,
            pady=5
        )
        close_btn.pack(pady=10)
        
        # Center window on screen
        stack_window.update_idletasks()
        x = (stack_window.winfo_screenwidth() // 2) - (stack_window.winfo_width() // 2)
        y = (stack_window.winfo_screenheight() // 2) - (stack_window.winfo_height() // 2)
        stack_window.geometry(f"+{x}+{y}")

    def library_imported(self, name):
        """Handle a library import event and populate keyword list for the executor tab.
        If the library fails to load or parse, skip it.
        """
        try:
            # ✅ Normalize path if it's a file-based library
            if os.path.isfile(name) or name.endswith(".py"):
                normalized_name = os.path.splitext(os.path.basename(name))[0]
            else:
                normalized_name = name

            # ✅ Check if library already loaded to avoid duplicate processing
            if normalized_name in self.libraries:
                logging.debug(f"[Debugger GUI] Library '{normalized_name}' already loaded, skipping")
                return

            libdoc = LibraryDocumentation(normalized_name)
            keywords = [{'name': kw.name, 'args': kw.args, 'doc': kw.doc} for kw in libdoc.keywords]
            self.libraries[libdoc.name] = keywords
            logging.info(f"[Debugger GUI] Loaded library: {libdoc.name} with {len(keywords)} keywords")
            
            # ✅ Update status indicator
            if hasattr(self, 'library_status_var'):
                lib_count = len(self.libraries)
                kw_count = sum(len(kws) for kws in self.libraries.values())
                self.library_status_var.set(f"[LIBS] {lib_count} libraries, {kw_count} keywords loaded")

            # ✅ Refresh dropdown only if custom tab is ready
            if getattr(self, "executor_ready", False):
                self._refresh_library_dropdown()
            
            # ✅ Refresh ignore keywords list to include new library keywords
            if hasattr(self, '_all_keywords'):
                self._refresh_ignore_keyword_list()

        except ImportError as e:
            logging.warning(f"[Debugger GUI] Library '{name}' not found: {e}")
        except Exception as e:
            logging.warning(f"[Debugger GUI] Failed to load library '{name}': {e}")

    def _refresh_library_dropdown(self):
        """Refresh the library and keyword dropdowns in the Custom Keyword tab."""

        required = ["library_dropdown", "keyword_dropdown", "doc_display"]
        if not all(hasattr(self, attr) for attr in required):
            return  # GUI not ready yet

        lib_names = sorted(self.libraries.keys())
        self.library_dropdown["values"] = lib_names
        
        # Only auto-select if we have libraries and nothing is selected
        if not self.library_var.get() and lib_names:
            self.library_var.set(lib_names[0])
            self._on_library_selected()
            
        current = self.library_dropdown.get()
        if current not in lib_names:
            self.library_dropdown.set('')
            self.keyword_dropdown.set('')
            self.keyword_dropdown["values"] = []

            # Clear argument editor
            for widget in self.custom_args_frame.winfo_children():
                widget.destroy()
            self.custom_arg_vars = []

            # Clear doc
            self.doc_display.config(state=tk.NORMAL)
            self.doc_display.delete("1.0", tk.END)
            self.doc_display.config(state=tk.DISABLED)

    def start(self):
        self.root.mainloop()

    # def _on_skip_keyword(self):
    #     # ✅ Debounce: If button is already disabled, return immediately
    #     if hasattr(self, "skip_continue_btn") and str(self.skip_continue_btn['state']) == 'disabled':
    #         return
    #
    #     # Disable buttons immediately to prevent multiple actions
    #     if hasattr(self, "retry_btn"):
    #         self.retry_btn.config(state=tk.NORMAL)
    #     if hasattr(self, "skip_kw_btn"):
    #         self.skip_kw_btn.config(state=tk.NORMAL)
    #
    #     self.update_status("Skipping keyword and continuing...", "goldenrod")
    #
    #     def do_skip():
    #         # ✅ Mark keyword as skipped
    #         self.core.skip_keyword = True
    #         self.core.continue_event.set()
    #
    #         # ✅ Visual log entry
    #         if self.core.failed_keyword:
    #             self._update_failure_display(
    #                 f"Keyword skipped by user.\nName: {self.core.failed_keyword.name}",
    #                 f"[{self.core.current_test}] Skip Keyword",
    #                 "pass"
    #             )
    #
    #         self.update_status("Keyword skipped. Test continued.", "green")
    #
    #     self.root.after(0, do_skip)
    def _on_skip_keyword(self):
        # Prevent multiple clicks while processing
        if hasattr(self, "skip_kw_btn") and str(self.skip_kw_btn['state']) == 'disabled':
            return

        # Safety check
        if not self.core.failed_keyword:
            messagebox.showwarning("Warning", "No failed keyword to skip.")
            return

        # Disable buttons while skipping
        if hasattr(self, "retry_btn"):
            self.retry_btn.config(state=tk.DISABLED)
        if hasattr(self, "skip_kw_btn"):
            self.skip_kw_btn.config(state=tk.DISABLED)

        self.update_status("Skipping keyword and continuing...", "goldenrod")

        def do_skip():
            try:
                # Mark keyword as skipped
                self.core.skip_keyword = True
                self.core.continue_event.set()

                # Log skip
                if self.core.failed_keyword:
                    self._update_failure_display(
                        f"Keyword skipped by user.\nName: {self.core.failed_keyword.name}",
                        f"[{self.core.current_test}] Skip Keyword",
                        "pass"
                    )

                self.update_status("Keyword skipped. Test continued.", "green")
            finally:
                # Keep buttons disabled; they will re-enable on the next failure
                pass

        threading.Thread(target=do_skip, daemon=True).start()

    def log_keyword_event(self, action, name, args=None, status="pending", message=""):
        if status.lower() == "pending":
            return
        timestamp = datetime.now().strftime("%H:%M:%S")
        icons = {"start": "➡", "end": "⬅", "fail": "❌", "pass": "✅", "skip": "⏭️", "pending": "🕓"}

        tag = {"PASS": "pass", "FAIL": "fail", "SKIP": "pending"}.get(status.upper(), "pending")
        icon = icons.get(action, "📝")

        # Format args
        args_lines = ""
        if args:
            for i, arg in enumerate(args):
                args_lines += f"    Arg{i + 1}: {arg}\n"

        # Format message
        msg_block = f"      {message}\n" if message else ""

        # Compose final log block
        full_text = (
            f"[{timestamp}] {icon} {name}  [{status.upper()}]\n"
            f"{args_lines}"
            f"{msg_block}"
            f"{'-' * 60}\n"
        )

        self.failure_text.config(state=tk.NORMAL)
        self.failure_text.insert(tk.END, f"[{timestamp}] {icon} {name}  [{status.upper()}]\n", ("header", tag))
        self.failure_text.insert(tk.END, args_lines, tag)
        if msg_block:
            self.failure_text.insert(tk.END, msg_block, tag)
        self.failure_text.insert(tk.END, f"{'-' * 60}\n", tag)
        self.failure_text.see(tk.END)
        self.failure_text.config(state=tk.DISABLED)

    def _setup_variable_tab(self):
        from tkinter import StringVar

        # === Layout using grid instead of mix of pack/grid ===
        self.var_tab.columnconfigure(0, weight=1)
        self.var_tab.rowconfigure(1, weight=1)

        # --- Top Bar: Search + Refresh ---
        control_frame = tk.Frame(self.var_tab)
        control_frame.grid(row=0, column=0, sticky="ew", padx=10, pady=5)
        control_frame.columnconfigure(1, weight=1)

        tk.Label(control_frame, text="Search:").grid(row=0, column=0, sticky="w")
        self.var_search_var = StringVar()
        search_entry = tk.Entry(control_frame, textvariable=self.var_search_var, width=30)
        search_entry.grid(row=0, column=1, sticky="ew", padx=5)
        search_entry.bind("<KeyRelease>", lambda e: self._refresh_variable_view())

        tk.Button(control_frame, text=" Refresh", command=self._refresh_variable_view).grid(row=0, column=2)

        # --- Treeview for Variables ---
        self.variable_tree = ttk.Treeview(self.var_tab)
        self.variable_tree.grid(row=1, column=0, sticky="nsew", padx=10, pady=5)
        self.variable_tree["columns"] = ("value", "type")
        self.variable_tree.heading("#0", text="Variable")
        self.variable_tree.heading("value", text="Value")
        self.variable_tree.heading("type", text="Type")
        self.variable_tree.column("value", width=350)
        self.variable_tree.column("type", width=100)
        self.variable_tree.bind("<<TreeviewSelect>>", self._on_variable_select)

        # --- Editor Section ---
        editor = tk.LabelFrame(self.var_tab, text="Create or Update Variable")
        editor.grid(row=2, column=0, sticky="ew", padx=10, pady=10)
        editor.columnconfigure(1, weight=1)

        tk.Label(editor, text="Name:").grid(row=0, column=0, padx=5, sticky="e")
        self.var_name_var = StringVar()
        self.var_name_entry = tk.Entry(editor, textvariable=self.var_name_var, width=40)
        self.var_name_entry.grid(row=0, column=1, padx=5, pady=5, sticky="w")

        tk.Label(editor, text="Note: No need to include ${}, it will be added automatically.", fg="gray",
                 font=("Segoe UI", 9)).grid(row=1, column=1, sticky="w", padx=5, pady=(0, 5))

        tk.Label(editor, text="Value:").grid(row=2, column=0, padx=5, sticky="e")
        self.var_value_var = StringVar()
        self.var_value_entry = tk.Entry(editor, textvariable=self.var_value_var, width=60)
        self.var_value_entry.grid(row=2, column=1, padx=5, pady=5, sticky="w")

        tk.Button(editor, text="Set Variable", command=self._set_variable_from_editor).grid(
            row=2, column=2, padx=10)

    def _refresh_variable_view(self):
        from robot.libraries.BuiltIn import BuiltIn
        search = self.var_search_var.get().lower()
        self.variable_tree.delete(*self.variable_tree.get_children())

        # Check if execution context is available
        if not self._has_active_execution_context():
            self.variable_tree.insert("", "end", 
                text="[PAUSE] No active test execution", 
                values=("Start a test to view variables", ""))
            return

        try:
            all_vars = BuiltIn().get_variables()

            for name, value in sorted(all_vars.items()):
                name_str = str(name)
                value_str = str(value)
                vtype = type(value).__name__

                if search and (search not in name_str.lower() and search not in value_str.lower()):
                    continue

                display_value = value_str[:100] + "..." if len(value_str) > 100 else value_str
                self.variable_tree.insert("", "end", text=name_str, values=(display_value, vtype))

        except RuntimeError as e:
            # Execution context not available (test ended or not started)
            if "Cannot access execution context" in str(e):
                self.variable_tree.insert("", "end", 
                    text="[WARN] Execution context lost", 
                    values=("Test may have ended", ""))
                logging.debug("[Debugger GUI] Variable refresh skipped - no execution context")
            else:
                self.variable_tree.insert("", "end", 
                    text="[ERROR] Error loading variables", 
                    values=(str(e), ""))
                logging.error(f"[Debugger GUI] Variable refresh error: {e}")
        except Exception as e:
            self.variable_tree.insert("", "end", 
                text="[ERROR] Unexpected error", 
                values=(str(e)[:100], ""))
            logging.error(f"[Debugger GUI] Variable refresh unexpected error: {e}", exc_info=True)

    def _has_active_execution_context(self):
        """Check if Robot Framework execution context is available"""
        try:
            from robot.libraries.BuiltIn import BuiltIn
            # Try to access context without calling get_variables
            bi = BuiltIn()
            _ = bi._context  # Access internal context
            return True
        except (RuntimeError, AttributeError):
            return False
            self._update_failure_display(f"Variable load failed: {e}", "[Variables]", "fail")

    def _on_variable_select(self, event):
        selected = self.variable_tree.selection()
        if not selected:
            return
        item = selected[0]
        name = self.variable_tree.item(item, "text")
        value = self.variable_tree.set(item, "value")

        self.var_name_var.set(name)
        self.var_value_var.set(value)

    def _set_variable_from_editor(self):
        from robot.libraries.BuiltIn import BuiltIn
        name = self.var_name_var.get().strip()
        value_str = self.var_value_var.get().strip()

        if not name.startswith("${"):
            name = "${" + name.strip("${}") + "}"  # auto-wrap

        try:
            # Use parse_arg for proper type conversion
            value = self.core.parse_arg(value_str)
            BuiltIn().set_test_variable(name, value)

            # ✅ Correct logging format — avoid retry/keyword confusion
            self._update_failure_display(
                text=f"Set variable: {name} = {value!r}",
                prefix="[Variables]",
                status="pass",
                keyword_name="Set Variable",
                args=[name, value]
            )

            self._refresh_variable_view()
            self.var_name_var.set(name)
            self.var_value_var.set("")
        except Exception as e:
            self._update_failure_display(
                text=f" Failed to set variable {name}: {e}",
                prefix="[Variables]",
                status="fail",
                keyword_name="Set Variable",
                args=[name, value_str]
            )

    def log_suite_start(self, data):
        log_suite_start(self, data)

    def log_suite_end(self, data, result):
        log_suite_end(self, data, result)

    def log_test_start(self, data):
        log_test_start(self, data)

    def log_test_end(self, data, result):
        log_test_end(self, data, result)

    def safe_close(self):
        """Safely close the GUI and unblock Robot Framework if waiting."""
        try:
            # Check if test is waiting for action
            if not self.core.continue_event.is_set():
                response = messagebox.askyesnocancel(
                    "Debugger Closing",
                    "Test execution is waiting for action.\n\n"
                    "Yes: Continue test and close debugger\n"
                    "No: Abort suite and close debugger\n"
                    "Cancel: Keep debugger open"
                )
                
                if response is None:  # Cancel
                    return
                elif response:  # Yes - continue
                    self.core.continue_event.set()
                    logging.info("[Debugger] User closed window - continuing test")
                else:  # No - abort
                    self.core.abort_suite = True
                    self.core.continue_event.set()
                    logging.warning("[Debugger] User closed window - aborting suite")
            
            # Stop any running timers
            self._stop_variable_refresh()
            
            # Close the window
            self.root.after(0, self.root.quit)
        except Exception as e:
            logging.warning(f"GUI close failed: {e}")
            try:
                self.root.destroy()
            except:
                pass

    def _emergency_exit(self, event=None):
        """Emergency exit that unblocks test execution (Ctrl+Q)"""
        response = messagebox.askyesno(
            "Emergency Exit",
            "Force continue test execution?\n\n"
            "This will unblock the test and close the debugger.\n"
            "Use this if the debugger is stuck or unresponsive."
        )
        if response:
            self.core.continue_event.set()
            self.core.abort_suite = False  # Clear abort flag
            self._stop_variable_refresh()
            logging.warning("[Debugger] Emergency exit triggered - force continuing")
            try:
                self.root.destroy()
            except:
                pass

    def schedule_variable_refresh(self, delay_ms=None):
        if delay_ms is None:
            delay_ms = self.VARIABLE_REFRESH_DELAY_MS
        if not hasattr(self, "_variable_refresh_scheduled") or not self._variable_refresh_scheduled:
            self._variable_refresh_scheduled = True
            self.root.after(delay_ms, self._perform_variable_refresh)

    def _perform_variable_refresh(self):
        self._variable_refresh_scheduled = False
        try:
            self._refresh_variable_view()
        except Exception as e:
            import logging
            logging.warning(f"Variable refresh failed: {e}")

    def _log_custom_executor_result(self, text, status="pass"):
        """
        Logs custom executor results without touching Retry tab widgets.
        """
        timestamp = datetime.now().strftime("%H:%M:%S")
        icons = {"pass": "✅", "fail": "❌", "pending": "🕓"}
        icon = icons.get(status, "🕓")

        full_text = (
            f"[{timestamp}] {icon} Custom Keyword Executor\n"
            f"  {text}\n"
            f"{'-' * 60}\n"
        )

        self.failure_text.config(state=tk.NORMAL)
        self.failure_text.insert(tk.END, full_text, status)
        self.failure_text.see(tk.END)
        self.failure_text.config(state=tk.DISABLED)
        self._trim_failure_log()

