"""A simulated robot may run on real hardware; telemetry must describe that host."""
import pytest
from convoy_agent import hardware
from convoy_agent.agent import Agent, AgentConfig, enroll
from convoy_agent.client import Client


@pytest.mark.parametrize("host_inventory", [False, True])
def test_simulator_host_discovery_survives_agent_restart(tmp_path, monkeypatch, host_inventory):
    reports = []
    measured = {"arch": "aarch64", "os": "linux", "mem_total_mb": 7619, "cpu_count": 6, "simulated": False}
    monkeypatch.setattr(hardware, "inventory", lambda: measured)
    monkeypatch.setattr(hardware, "boot_id", lambda: "actual-boot")
    monkeypatch.setattr(hardware, "read_meminfo", lambda: {"mem_total_mb": 7619, "mem_available_mb": 1234, "swap_total_mb": 0, "swap_free_mb": 0})

    def post(self, path, body, **kwargs):
        reports.append((path, body))
        return {"device_id": "dev_host"} if path.endswith("enroll") else {"applied": True}

    monkeypatch.setattr(Client, "post", post)
    enroll(tmp_path, server="http://127.0.0.1:1", token="one-use", name="Simulator", simulate=True,
           host_inventory=host_inventory)
    reported = reports[-1][1]
    assert reported["simulated"] is True
    assert reported["hardware"]["simulated"] is not host_inventory
    if host_inventory:
        assert reported["hardware"] == measured
        assert AgentConfig(tmp_path).data["robot_sim"] is False
    for _ in range(2):
        agent = Agent(tmp_path)
        try:
            assert type(agent.sensors) is (hardware.Sensors if host_inventory else hardware.SimulatedSensors)
            assert agent.robot_wanted is not host_inventory
            # Inspect the outgoing heartbeat, not just its internal inventory field.
            for _ in range(20):
                agent.report()
                heartbeat = reports[-1][1]
                if heartbeat["hardware"] is not None:
                    break
            assert heartbeat["hardware"]["simulated"] is not host_inventory
            assert heartbeat["time_confidence"] == ("unknown" if host_inventory else "simulated")
            if host_inventory:
                assert heartbeat["boot_id"] == "actual-boot"
                assert heartbeat["telemetry"]["mem_total_mb"] == 7619
        finally:
            agent.journal.close()
