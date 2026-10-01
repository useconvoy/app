"""The uploader resumes acknowledged commands without ever invoking an adapter."""
import json
import sqlite3

import pytest
from convoy_agent.coordinator.recordings import sync_recordings


def test_upload_resumes_after_lost_ack_and_never_reads_other_binding(tmp_path):
    journal = tmp_path / 'execution.sqlite3'
    progress = tmp_path / 'progress.json'
    observation = {'image_png_base64':'placeholder'}
    with sqlite3.connect(journal) as db:
        db.executescript('CREATE TABLE meta(key,value); CREATE TABLE missions(id,report_json,reported,state); CREATE TABLE commands(mission_id,sequence,request_json,result_json,observation_json,state);')
        db.execute('INSERT INTO meta VALUES (?,?)', ('binding',json.dumps({'robot_id':'rob_one','device_id':'dev_one'})))
        db.execute('INSERT INTO missions VALUES (?,?,?,?)', ('mis_one',json.dumps({'summary':{'steps':2}}),1,'completed'))
        for seq in range(2):
            db.execute('INSERT INTO commands VALUES (?,?,?,?,?,?)', ('mis_one',seq,json.dumps({'observation':observation}),'{}','{}','applied'))
    calls = []

    class Control:
        fail = True

        def post(self, path, body):
            calls.append(path)
            if path.endswith('/commands/1') and self.fail:
                raise RuntimeError('lost acknowledgement')
            return {}

    control = Control()
    with pytest.raises(RuntimeError):
        sync_recordings(journal,'rob_one','dev_one',control,progress)
    assert json.loads(progress.read_text()) == {'mis_one':1}
    control.fail = False
    sync_recordings(journal,'rob_one','dev_one',control,progress)
    assert [p.rsplit('/',1)[-1] for p in calls] == ['0','1','1','publish']
    sync_recordings(journal,'rob_one','dev_one',control,progress)
    assert len(calls) == 4
    with pytest.raises(ValueError):
        sync_recordings(journal,'rob_other','dev_one',control,progress)
    assert len(calls) == 4
