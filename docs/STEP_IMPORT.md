# STEP assembly import

The independent importer extension normalises component relationships using
STEP product/occurrence metadata and the coordinate-frame membership of each
representation. It supports either relationship direction, reused component
definitions and composed transforms through nested assemblies. Existing direct
spherical-surface imports retain the original parser's behaviour and units.

Ambiguous frame ownership, cyclic or disconnected assemblies and non-finite
coordinates produce an explicit error rather than an inferred marker geometry.
The converter still requires the user to select and save points explicitly.
Loading a STEP never replaces a stored geometry or changes its origin/axes.

Tests use synthetic coordinate frames with independently calculated positions,
rotations, nonzero local origins and unit conversions. The packaged mock check
also loads synthetic STEP files through the real converter dialog and verifies
that stored geometry files are unchanged. Real CAD/calibration files remain
local and are excluded from source releases.
