import pytest

from isite2.domain.enums import SceneForm
from isite2.domain.models import PropertyEntity


def test_lat_lon_validation() -> None:
    with pytest.raises(ValueError):
        PropertyEntity(
            country="X",
            city="Y",
            property_name="Z",
            scene_type="airport_terminal",
            scene_form=SceneForm.INDOOR,
            latitude=100,
            longitude=10,
            geocode_precision="exact",
        )
