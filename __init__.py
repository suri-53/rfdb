"""
rfdb - Robot Framework Debugger

A powerful interactive debugger for Robot Framework with real-time test control,
variable inspection, and keyword retry capabilities.

Usage:
    # As a listener (recommended)
    robot --listener rfdb your_test.robot
    
    # As a library
    *** Settings ***
    Library    rfdb

Author: Suriya
License: MIT
Version: 2.0
"""

from .RobotRetrier import RobotFrameworkDebugger

__version__ = "2.0"
__author__ = "Suriya"
__license__ = "MIT"

# Expose at package level for --listener rfdb
ROBOT_LISTENER_API_VERSION = 3

# Create a single shared instance
_instance = None

def __getattr__(name):
    """
    Delegate attribute access to a single RobotFrameworkDebugger instance.
    This allows Robot Framework to use 'rfdb' directly as a listener.
    """
    global _instance
    if _instance is None:
        _instance = RobotFrameworkDebugger()
    return getattr(_instance, name)

__all__ = ["RobotFrameworkDebugger"]
