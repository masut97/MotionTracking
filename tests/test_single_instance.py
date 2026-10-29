"""Only synthetic processes and per-test mutex names; never open a device."""
import multiprocessing as mp
import os
from types import SimpleNamespace as NS
import uuid

import pytest

from atracsys_sync import single_instance as single


@pytest.mark.parametrize('args,expected', [
    (['app'], False), (['app', '--mode', 'hardware'], False),
    (['app', '--mode', 'mock'], False),
    (['app', '--run-analysis', '', '--mode', 'hardware'], False),
    (['app', '--sync-worker', '1234', 'token'], True),
    (['app', '--run-analysis', 'script.py', '--analysis-input', 'test.csv'], True),
    (['app', '--sync-self-test', 'isolated-output'], True),
    (['app', '--sync-camera-self-test', 'isolated-output'], True),
    (['app', '--launch-after-exit', '123', 'new-app'], True)])
def test_only_internal_non_gui_modes_bypass_guard(args, expected):
    assert single.is_auxiliary(args) is expected


@pytest.mark.parametrize('args,auxiliary', [(['app','--mode','hardware'],False),
                                         (['app','--sync-worker','123','token'],True),
                                         (['app','--sync-self-test','isolated'],True)])
def test_bootstrap_checks_guard_before_any_application_setup(monkeypatch,args,auxiliary):
    from atracsys_sync import bootstrap
    calls=[]
    monkeypatch.setattr(bootstrap.sys,'argv',args)
    monkeypatch.setattr(bootstrap,'run_application',lambda:calls.append('application') or 7)
    monkeypatch.setattr(single,'run_gui',lambda run:calls.append('guard') or 0)
    assert bootstrap.main()==(7 if auxiliary else 0)
    assert calls==(['application'] if auxiliary else ['guard'])


@pytest.mark.parametrize('acquired,window,runs', [(True,None,True),(False,None,False),
                                               (False,42,False),(True,42,False)])
def test_startup_gate_precedes_application_initialisation(monkeypatch, acquired, window, runs):
    events = []
    guard = NS(acquire=lambda: events.append('lock') or acquired,
               close=lambda: events.append('close'))
    monkeypatch.setattr(single,'InstanceGuard',lambda:guard)
    monkeypatch.setattr(single,'existing_window',lambda:window)
    monkeypatch.setattr(single,'activate',lambda hwnd:events.append(('activate',hwnd)))
    monkeypatch.setattr(single,'notify',lambda text:events.append('notice'))
    assert single.run_gui(lambda:events.append('application') or 0) == 0
    assert ('application' in events) is runs
    assert events[0]=='lock' and events[-1]=='close'
    if window: assert ('activate',window) in events


def test_lock_access_failure_never_initialises_gui_or_camera(monkeypatch):
    def deny(): raise OSError('access denied')
    closed=[];notices=[]
    monkeypatch.setattr(single,'InstanceGuard',lambda:NS(acquire=deny,close=lambda:closed.append(True)))
    monkeypatch.setattr(single,'notify',notices.append)
    assert single.run_gui(lambda:pytest.fail('must not initialise application'))==1
    assert closed and notices


def mutex_owner(name, connection):
    guard=single.InstanceGuard(name)
    try:
        connection.send(guard.acquire())
        connection.recv()
    finally:
        guard.close()


@pytest.mark.skipif(os.name!='nt',reason='Windows kernel mutex required')
@pytest.mark.parametrize('crash',[False,True])
def test_second_process_blocked_and_lock_recovers_after_normal_exit_or_crash(crash):
    ctx=mp.get_context('spawn')
    name=r'Global\MotionTracking-synthetic-'+uuid.uuid4().hex
    parent,child=ctx.Pipe()
    process=ctx.Process(target=mutex_owner,args=(name,child))
    process.start();child.close()
    try:
        assert parent.poll(20) and parent.recv() is True
        contender=single.InstanceGuard(name)
        try: assert contender.acquire() is False
        finally: contender.close()
        if crash: process.terminate()
        else: parent.send('stop')
        process.join(15);assert not process.is_alive()
        replacement=single.InstanceGuard(name)
        try: assert replacement.acquire() is True
        finally: replacement.close()
    finally:
        if process.is_alive():process.terminate();process.join(10)
        parent.close()
