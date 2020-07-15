import pytest
from asynctest import CoroutineMock

from paradox.hardware import Panel
from paradox.paradox import Paradox


@pytest.mark.asyncio
async def test_send_panic(mocker):
    alarm = Paradox()
    alarm.panel = mocker.Mock(spec=Panel)
    alarm.panel.send_panic = CoroutineMock()

    alarm.storage.get_container("partition").deep_merge({1: {"id": 1, "key": "Partition 1"}})
    alarm.storage.get_container("user").deep_merge({3: {"id": 3, "key": "User 3"}})

    assert await alarm.send_panic("1", "fire", "3")
    alarm.panel.send_panic.assert_called_once_with(1, "fire", 3)
    alarm.panel.send_panic.reset_mock()

    assert await alarm.send_panic("Partition 1", "fire", "User 3")
    alarm.panel.send_panic.assert_called_once_with(1, "fire", 3)



