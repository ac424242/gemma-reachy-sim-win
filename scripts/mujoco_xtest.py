"""Minimal MuJoCo viewer test to validate OpenGL rendering through VcXsrv.

Opens a passive viewer with a trivial model for a few seconds. If a window
appears on the Windows desktop (and this prints VIEWER_OK), the GL/X path works
and the Reachy Mini sim window will too. If GLFW/GLX errors out, the display
path - not reachy-mini - is the blocker.
"""

import time

import mujoco
import mujoco.viewer

XML = """
<mujoco>
  <option gravity="0 0 -9.81"/>
  <worldbody>
    <light pos="0 0 3"/>
    <geom type="plane" size="2 2 0.1" rgba="0.6 0.6 0.6 1"/>
    <body pos="0 0 1">
      <freejoint/>
      <geom type="box" size="0.2 0.2 0.2" rgba="0.2 0.5 0.9 1"/>
    </body>
  </worldbody>
</mujoco>
"""


def main() -> None:
    model = mujoco.MjModel.from_xml_string(XML)
    data = mujoco.MjData(model)
    print("MODEL_LOADED")
    with mujoco.viewer.launch_passive(model, data) as viewer:
        print("VIEWER_OK")
        start = time.time()
        while viewer.is_running() and time.time() - start < 6.0:
            mujoco.mj_step(model, data)
            viewer.sync()
            time.sleep(0.01)
    print("VIEWER_CLOSED")


if __name__ == "__main__":
    main()
