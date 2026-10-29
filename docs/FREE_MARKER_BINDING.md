# Geometry and instance selection in free analysis

Some SDK builds change tracking IDs between consecutive observations. These
numbers must not be presented as permanent identities of physical objects.

For one recognised instance per geometry, a new live profile prefers geometry
IDs such as `299`, `302`, `351`. The source dropdown also permits an explicit
`geometry:tracking` selection. Reference, base and target remain user-defined.

Existing profiles are not silently rebound. In the live profile editor,
**Eindeutige Geometrien verwenden** explicitly converts eligible rows to their
geometry IDs, preserving names, reference and comparison assignments. A source
can also be changed individually in its dropdown. Already selected bindings
must not overlap a geometry-only selector and an explicit instance of the same
geometry. Missing selections remain visibly unavailable.

Geometry-only analysis accepts changing tracking IDs, but requires exactly one
unambiguous valid pose for that geometry in each received frame. Simultaneous
instances or duplicate poses invalidate that frame; missing observations are
never filled. Explicit tracking selectors never substitute another tracking ID.
Other ROM-loader callers retain their previous stricter default policy.

A geometry-only selector is not proof of physical identity across a replacement
of one object by another using the same geometry. Multiple physical objects
sharing a geometry and unstable SDK IDs cannot be reliably identified from these
numbers alone. Do not assign them automatically by their order in a table.

CSV marker import examines simultaneous instances per recorded frame. It no
longer adds thousands of apparent instances merely because tracking IDs changed
over time. Existing measurements, timestamps and profiles remain unchanged
until the user explicitly saves a profile. Free reports record the applied
identity policy.
