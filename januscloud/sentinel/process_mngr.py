"""
januscloud.sentinel.process_mngr
~~~~~~~~~~~~~~~~~~~~~~~

This module implements the process watchers and some related
helper functions

:copyright: (c) 2015 by OpenSight (www.opensight.cn).
:license: AGPLv3, see LICENSE for more details.

"""

from __future__ import unicode_literals, division, print_function
import gevent
from gevent import subprocess, sleep
import sys
import os
import time
from januscloud.common.error import JanusCloudError
import weakref
import traceback
import logging

log = logging.getLogger(__name__)

PROC_STOP = 0
PROC_RUNNING = 1

PROC_STATUS_TEXT = ['STOP', 'RUNNING']

POLL_INTERVAL_SEC = 0.1
DEFAULT_STOP_WAIT_TIMEOUT = 3

if sys.version_info[:2] >= (3, 3):
    DEVNULL = subprocess.DEVNULL
else:
    DEVNULL = open(os.devnull, "r+")


_watchers = weakref.WeakValueDictionary()
_next_wid = 0



def _get_next_watcher_id():
    global _next_wid
    _next_wid += 1
    return _next_wid


class ProcWatcher(object):
    def __init__(self, args, 
                 error_restart_interval=30.0, age_time=0.0,
                 poll_interval=0.1, process_status_cb=None):
        """ ProcWatcher constructor

        Args:
        args: the process command args. args should be a sequence of program
            arguments or else a single string. By default, the program to
            execute is the first item in args if args is a sequence.
            If args is a string, the interpretation is platform-dependent.
            Unless otherwise stated, it is recommended to pass args as a
            sequence.

        """
        self.wid = _get_next_watcher_id()
        self.args = args
        self.process_return_code = 0
        self.process_exit_time = 0
        self.auto_restart_count = 0
        self.age_time = float(age_time)
        self._proc_start_time = 0
        self._error_restart_interval = float(error_restart_interval)
        self._popen = None
        self._started = False
        self._process_status_cb = process_status_cb
        self._poll_greenlet = None
        self._poll_interval = poll_interval
        self._has_aged = False

        # add to the manager
        global _watchers
        if self.wid in _watchers:
            raise KeyError("wid already exist")
        _watchers[self.wid] = self

    def __str__(self):
        return ('ProcWatcher (wid:%s, args:%s, pid:%s, process_status:%d, '
                'process_return_code:%d, process_exit_time:%f, '
                'process_running_time:%f, restart_count:%d, '
                'age_time:%f, is_started:%s)') % \
               (self.wid, self.args, self.pid, self.process_status,
                self.process_return_code, self.process_exit_time,
                self.process_running_time, self.auto_restart_count,
                self.age_time, self.is_started())

    def __del__(self):
        self._started = False
        if self._popen is not None:
            try:
                # print("kill------------------")
                self._popen.kill()
            except OSError:
                pass
            finally:
                self._popen = None

    def _launch_process(self):

        self._popen = subprocess.Popen(self.args,
                                       stdin=DEVNULL,
                                       stdout=DEVNULL,
                                       stderr=DEVNULL,
                                       close_fds=True,
                                       shell=False)
        log.debug("lanch new process %s, pid:%d" % (self.args, self._popen.pid))
        self._has_aged = False
        self._proc_start_time = time.time()
        self._on_process_status_change()

    def _on_process_terminate(self, ret):
        log.warning("process pid:%d terminated with returncode:%d" % (self._popen.pid, ret))
        self._popen = None
        self.process_return_code = ret
        self.process_exit_time = time.time()
        self._proc_start_time = 0
        # self._has_aged = False
        # print(ret)
        self._on_process_status_change()

    def _on_process_status_change(self):
        try:
            cb = self._process_status_cb
            if cb is not None and callable(cb):
                cb(self)
        except Exception:
            pass

    @staticmethod
    def _polling_run(watcher_weakref):
        current = gevent.getcurrent()

        while True:
            # print("check")
            watcher = watcher_weakref()
            if (watcher is None) or (not watcher.is_started()) \
                or (watcher._poll_greenlet != current):
                return     # make greenlet exit
            sleep_time = watcher._poll_interval
            try:
                if watcher._popen is None:
                    # restart
                    watcher.auto_restart_count += 1
                    watcher._launch_process()
                else:
                    # check the child process
                    ret = watcher._popen.poll()
                    if ret is not None:
                        # the process terminate
                        watcher._on_process_terminate(ret)
                        if watcher._error_restart_interval > 0:
                            if ret != 0:
                                # exit with error
                                sleep_time = watcher._error_restart_interval
                            else:
                                # exit normally
                                sleep_time = 0    # restart at once
                        else:
                            return   # if no need to restart, make the greenlet exit at once
                    else:
                        if watcher.age_time > 0:    # if age time present, check age
                            now = time.time()
                            if watcher._proc_start_time > now: # check time is changed
                                watcher._proc_start_time = now
                            if watcher._has_aged:
                                if now - watcher._proc_start_time > watcher.age_time + 5: # terminate no effect, kill it
                                    try:
                                        watcher._popen.kill()
                                    except OSError:
                                        pass
                            else:
                                if now - watcher._proc_start_time > watcher.age_time:
                                    watcher._has_aged = True
                                    try:
                                        watcher._popen.terminate()
                                    except OSError:
                                        pass
            except Exception as e:
                log.exception("process polling greenlet receives the below Exception when running, ignored")
                pass
            del watcher
            sleep(sleep_time)      # next time to check


    @staticmethod
    def _terminate_run(popen, wait_timeout):
        
        ret = None
        try:
            ret = popen.wait(wait_timeout)
        except subprocess.TimeoutExpired:
            ret = None
        except Exception:
            pass

        #check terminated successfully, if not, kill it
        if ret is None:
            # time out, force child process terminate
            try:
                popen.kill()
            except OSError:
                pass
            popen.wait()
    
    @property
    def pid(self):
        if self._popen is not None:
            return self._popen.pid
        else:
            return None

    @property
    def process_status(self):
        if self._popen is not None:
            return PROC_RUNNING
        else:
            return PROC_STOP

    @property
    def process_running_time(self):
        if self._popen is not None:
            return time.time() - self._proc_start_time
        else:
            return 0

    def is_started(self):
        return self._started

    def start(self):
        """ start the watcher and launch its process
        
        """
        if self._started:
            return

        if self._popen is not None:
            # other greenlet is stopping this watcher
            raise JanusCloudError("ProcWatcher (%d) in Stopping" % self.wid, 500)

        try:
            self._launch_process()   # start up the process

            self.auto_restart_count = 0
            self._started = True

            # spawn a poll greenlet to watch it
            self._poll_greenlet = gevent.spawn(self._polling_run, weakref.ref(self))

        except Exception:
            self._started = False
            self._poll_greenlet = None
            if self._popen is not None:
                self._popen.kill()
                self._on_process_terminate(-1)
            raise

    def stop(self, wait_timeout=DEFAULT_STOP_WAIT_TIMEOUT):
        """ Stop the watcher and wait until the related process terminates
    
        Stop this watcher, send SIGTERM signal to the process, 
        wait until the process exits or wait_timeout is due, 
        if the process has not yet terminated, kill it. 
    
        After this function return, the process should have been terminated,
        and process_return_code would be set to the actual value
    
        Args:
            self: watcher instance
            wait_timeout: the time to wait for the process's termination before kill it
        
        """
        if not self._started:
            return

        self._started = False
        self._poll_greenlet = None # detach the polling greenlet

        if self._popen is not None:
            # terminate the process normally at first
            popen = self._popen

            try:
                popen.terminate()
            except OSError:
                pass

            ret = None
            try:
                ret = popen.wait(wait_timeout)
            except subprocess.TimeoutExpired:
                ret = None
            except Exception:
                pass
            
            #check terminated successfully, if not, kill it
            if ret is None:
                # time out, force child process terminate
                try:
                    popen.kill()
                except OSError:
                    pass
                ret = popen.wait()

            if popen is self._popen: # the same process after wait
                self._on_process_terminate(ret)
    
    def async_stop(self, wait_timeout=DEFAULT_STOP_WAIT_TIMEOUT):
        """ Stop the watcher, and terminate the process async
    
        After this function return, the watcher has been stopped, but the process 
        may or may not have been terminated. It postpone the process termination 
        waiting operation in another new greenlet. The process_return_code attribute
        would be set to 0.
    
        Args:
            wait_timeout: the time to wait for the process's termination before kill it
        
        """
        if not self._started:
            return

        self._started = False
        self._poll_greenlet = None # detach the polling greenlet

        if self._popen is not None:
            ret = 0
            try:
                self._popen.terminate()
                gevent.spawn(ProcWatcher._terminate_run, self._popen, wait_timeout)
            except OSError:
                ret = self._popen.poll()


            self._on_process_terminate(ret)
    
    def destroy(self):
        self.async_stop(DEFAULT_STOP_WAIT_TIMEOUT)
        self._process_status_cb = None  # release ref of the callback


def spawn_watcher(*args, **kwargs):
    """ create asd start a process watcher instance

    Args:
        The arguments are passed to `ProcWatcher.__init__`

        args: the process command args. args should be a sequence of program
            arguments or else a single string. By default, the program to
            execute is the first item in args if args is a sequence.
            If args is a string, the interpretation is platform-dependent.
            Unless otherwise stated, it is recommended to pass args as a
            sequence.
        restart_interval: time in sec to restart the process after its error termination.
            if the process exit with 0 exit_code, it would be restart immediately.
            

    Returns:
        A started Watcher instance related to the args which is already schedule

    Raises:
        OSError: when trying to execute a non-existent file
    """
    watcher = ProcWatcher(*args, **kwargs)
    try:
        watcher.start()
    except Exception:
        watcher.destroy()
        raise

    return watcher


def list_all_waitcher():
    return list(_watchers.values())


def find_watcher_by_wid(wid):
    return _watchers.get(wid)


def kill_all(name):
    kill_all_popen = subprocess.Popen(["killall", "-9", name],
                                       stdin=DEVNULL,
                                       stdout=DEVNULL,
                                       stderr=DEVNULL,
                                       close_fds=True,
                                       shell=False)

    try:
        ret = kill_all_popen.wait(DEFAULT_STOP_WAIT_TIMEOUT)
    except subprocess.TimeoutExpired:
        ret = None

    if ret is None:
        kill_all_popen.kill()
        kill_all_popen.wait()

def test_process_status_cb(watcher):
    print("watcher status changed:", watcher)


def test_main():
    print("create ls watcher")
    ls_watcher = spawn_watcher(["ls"], process_status_cb=test_process_status_cb)
    print(ls_watcher)
    assert(ls_watcher.pid is not None)
    assert(ls_watcher.process_status == PROC_RUNNING)
    assert(ls_watcher.process_running_time > 0)
    assert(ls_watcher.is_started())
    print("enter sleep")
    sleep(0.5)
    # the process should terminated
    print("after 0.5s:")
    print(ls_watcher)
    assert(ls_watcher.pid is None)
    assert(ls_watcher.process_status == PROC_STOP)
    assert(ls_watcher.process_running_time == 0)
    assert(ls_watcher.process_return_code == 0)
    assert(ls_watcher.process_exit_time != 0)
    assert(ls_watcher.is_started())
    print("enter sleep")
    sleep(1)
    # the process should restart
    print("after 1.5s:")
    print(ls_watcher)
    assert(ls_watcher.auto_restart_count > 0)
    ls_watcher.stop()
    ls_watcher.destroy()
    del ls_watcher

    print("create \"sleep\" watcher")
    sleep_watcher = spawn_watcher(["sleep", "5"], error_restart_interval=1)
    print(sleep_watcher)
    org_pid = sleep_watcher.pid
    assert(sleep_watcher.pid is not None)
    assert(sleep_watcher.process_status == PROC_RUNNING)
    assert(sleep_watcher.process_running_time > 0)
    assert(sleep_watcher.is_started())
    sleep(1)

    print("stop \"sleep\" watcher")
    sleep_watcher.stop()
    print(sleep_watcher)
    assert(sleep_watcher.pid is None)
    assert(sleep_watcher.process_status == PROC_STOP)
    assert(sleep_watcher.process_running_time == 0)
    assert(sleep_watcher.process_exit_time != 0)

    print("start \"sleep\" watcher")
    sleep_watcher.start()
    print(sleep_watcher)
    assert(sleep_watcher.pid is not None)
    assert(sleep_watcher.pid != org_pid)
    assert(sleep_watcher.process_status == PROC_RUNNING)
    assert(sleep_watcher.process_running_time > 0)
    assert(sleep_watcher.is_started())
    print("enter sleep")
    sleep(1)

    sleep_watcher.destroy()

