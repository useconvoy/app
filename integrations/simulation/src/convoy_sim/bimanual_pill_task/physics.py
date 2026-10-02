"""Physical constants for the pill task, in SI units, with the reasoning for each.

The values are engineering estimates for the materials involved, not measurements
of a specific robot or product. ``docs/bimanual-pill-task.md`` explains them, and
``check_physics`` in ``scene.py`` measures settling, penetration and tunnelling with
these exact values.
"""

from __future__ import annotations

from dataclasses import dataclass

# --- Integration -------------------------------------------------------------
# 2 ms steps (1 ms selectable with --timestep). The fastest pill motion in the
# task is a drop into the bottle from ~4 cm (0.9 m/s, 1.8 mm per step) or being
# carried at <=0.6 m/s (1.2 mm per step): well under the 4 mm pill radius and the
# 9.5 mm a pill would have to travel in one step to cross a 1.5 mm bottle wall,
# so discrete collision detection cannot step through a wall, the floor of the
# bottle or the mat. check_physics() also drops pills from 0.3 m (2.4 m/s).
# 1 ms and 2 ms give the same task outcomes on the checked seeds (see the docs).
TIMESTEP_S = 0.002
# implicitfast integrates actuator damping and joint damping implicitly, so stiff
# servo gains stay stable at 1 ms. Conjugate gradient instead of Newton: with ~25
# free pills (nv ~174) and up to ~550 contact rows once pills pile up in the
# bottle, Newton's dense nv x nv Hessian costs ~6-10 ms per step, CG ~1.5 ms, and
# at a 1e-10 tolerance both leave resting pills creeping below 1 mm/s.
INTEGRATOR = "implicitfast"
SOLVER = "CG"
SOLVER_ITERATIONS = 200
SOLVER_TOLERANCE = 1e-10
# Elliptic friction cones model Coulomb friction exactly (no pyramid facets) and
# impratio=10 makes the friction rows stiffer than the normal rows, which is the
# standard MuJoCo setting for slip-free grasps of small parts.
CONE = "elliptic"
IMPRATIO = 10.0
# MuJoCo's soft contacts let a squeezed 0.5-1 g capsule creep and rotate between
# the pads (the constraint force needs a small slip velocity to build up). The
# noslip post-pass re-solves the friction rows without softness, so a held pill
# stays put through a 0.6 m/s transfer: with 0 iterations 13 of 230 checked
# transfers lost their pill on the way to the bottle, with 4 none of 797 did
# (docs/bimanual-pill-task.md), for ~15% more compute per step.
NOSLIP_ITERATIONS = 4

# --- Contact softness ----------------------------------------------------------
# MuJoCo contacts are a spring-damper in *acceleration* space, so the same solref
# works for a 0.7 g pill and a 6 kg arm. timeconst must be >= 2 timesteps; 4 ms
# (critically damped) is the stiffest stable choice at a 2 ms step (2x margin at
# 1 ms). The default (20 ms) lets a pill sink ~0.3 mm into the mat and wobble;
# 4 ms keeps the resting penetration at ~4 micrometres. Under a squeeze the
# penetration grows with force over the pill's tiny mass (~0.4 mm per pad at 3 N),
# comparable to the compression of a silicone finger pad.
CONTACT_SOLREF = (0.004, 1.0)
# Impedance ramps from 0.95 at first touch to 0.99 at 0.5 mm penetration (width).
# Values near 1 approach a hard constraint; 0.99 stays well-conditioned.
CONTACT_SOLIMP = (0.95, 0.99, 0.0005, 0.5, 2.0)


@dataclass(frozen=True)
class Friction:
    """MuJoCo geom friction: (sliding, torsional [m], rolling [m]).

    MuJoCo combines two geoms by taking the element-wise maximum, so each value
    below is chosen so the pair that matters gets the intended coefficient: the
    pill has the lowest sliding friction, so pill-on-X contacts use X's value.
    """

    sliding: float
    torsional: float
    rolling: float

    def mjcf(self) -> str:
        return f"{self.sliding:g} {self.torsional:g} {self.rolling:g}"


# Film-coated / gelatin capsule against another capsule: about 0.3 (coated
# tablets on smooth polymer measure 0.2-0.4). Rolling resistance of a 4 mm radius
# capsule on a slightly compliant surface: c_rr ~0.02 x r = 8e-5 m. Torsion: a
# ~1 mm contact patch gives ~(2/3) mu a = 4e-4 m.
PILL_FRICTION = Friction(0.30, 4e-4, 8e-5)
# Black rubber/silicone work mat against coated capsules: 0.6 (rubber on dry
# polymer typically 0.5-0.8). The mat is what keeps pills from skidding when a
# finger brushes them.
MAT_FRICTION = Friction(0.60, 4e-4, 8e-5)
# Laminate tabletop outside the mat.
TABLE_FRICTION = Friction(0.40, 4e-4, 8e-5)
# Soft TPU/silicone finger pads against a capsule: 0.9-1.0. The larger, softer
# patch also gives more torsional friction, which stops a held pill pivoting.
PAD_FRICTION = Friction(0.95, 1.6e-3, 1e-4)
# Rigid 3D-printed PLA/PETG finger and housing surfaces: ~0.35.
PRINTED_FRICTION = Friction(0.35, 4e-4, 8e-5)
# HDPE bottle and polypropylene cap: ~0.25 against polymers.
HDPE_FRICTION = Friction(0.25, 4e-4, 8e-5)
# Painted/anodised robot shells and floor.
SHELL_FRICTION = Friction(0.40, 4e-4, 8e-5)

# Pills use the full 6-D contact (sliding, torsion, rolling); without rolling
# resistance a capsule lying on its side rolls forever after the lightest nudge.
PILL_CONDIM = 6
PAD_CONDIM = 6
DEFAULT_CONDIM = 3

# --- Objects -----------------------------------------------------------------
# A 20 x 8 mm capsule: radius 4 mm, cylinder half-length 6 mm. Volume 0.871 cm^3,
# so 0.5-1.0 g is a partly filled hard capsule (density 570-1150 kg/m^3).
PILL_RADIUS_M = 0.004
PILL_HALF_LENGTH_M = 0.006
PILL_MASS_RANGE_KG = (0.0005, 0.0010)

# Wide-mouth supplement (packer) bottle: 65 mm body, 110 mm tall, 45 mm neck
# finish with a 41 mm opening, 1.5 mm HDPE wall; ~22 g empty.
BOTTLE_OUTER_RADIUS_M = 0.0325
BOTTLE_HEIGHT_M = 0.110
BOTTLE_WALL_M = 0.0015
BOTTLE_SHOULDER_Z_M = (0.088, 0.098)
BOTTLE_NECK_OUTER_RADIUS_M = 0.0225
BOTTLE_MOUTH_RADIUS_M = 0.0205
BOTTLE_MASS_KG = 0.022
# 48 mm polypropylene screw cap, 16 mm tall, ~4.5 g.
CAP_RADIUS_M = 0.024
CAP_HEIGHT_M = 0.016
CAP_MASS_KG = 0.0045

# --- Environment -------------------------------------------------------------
TABLE_HEIGHT_M = 0.75
MAT_THICKNESS_M = 0.003
MAT_TOP_M = TABLE_HEIGHT_M + MAT_THICKNESS_M
GRAVITY = 9.81
