# /// script
# requires-python = ">=3.12"
# dependencies = ["webknossos"]
# ///
import webknossos as wk
from webknossos.geometry import BoundingBox
bb = BoundingBox((0,0,0), (10,10,10))
print(dir(bb))
print(bb.size)
