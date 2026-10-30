"""An extensible quantitative research platform.

Organised as a modular monolith with one-directional layering::

    core -> data -> features -> representations -> models -> decisions
                                                          -> evaluation
                                                          -> simulation
    experiments (composition root, may import anything)

``core`` holds the domain objects and protocols every other layer agrees on
and imports nothing from them. That constraint is enforced by a test, not by
convention.
"""

__version__ = "0.1.0"
