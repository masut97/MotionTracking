# Measured fiducial export

New recordings also write `<name>.fiducials.csv` and a final
`<name>.fiducials.json` summary. Existing measurements are never rewritten.
Raw triangulated XYZ observations use camera coordinates in millimetres.
They are not transformed geometry points or inferred sphere positions.

The current native v2 bridge carries raw observations in stereo image frames,
not in its high-rate pose ring. This export therefore records received image
frames (currently at most 30 Hz under the default image limit), independently
of the selected pose recording frequency. It never upsamples, repeats old
observations or fills absent measurements. Original device timestamps, frame
counters and image-frame host timestamps are preserved. Join with pose data by
device timestamp and frame counter; do not join by CSV row number or assume
identical host timestamps for the two publication channels. The v2 bridge
supplies at most 256 triangulated observations in an image frame.

`frame_fiducial_index` is the SDK observation-array index for that frame; it is
not a stable physical sphere number. `geometry_fiducial_number` is 1-based in
the snapshotted geometry file's order. The original SDK correspondence table
is NOT exported by v2. Instead, numbering is explicitly marked
`unique_geometric_match`: a valid measured point and an observed geometry point
must be the only candidates for one another within 1 mm of the rigid-pose
prediction, across all recognised marker instances. The presence mask is
respected. This conservative limit can leave deformed or noisy points
unassigned; it is an association policy, not an accuracy specification or a
biomechanical quality threshold. It must not be interpreted as independent
confirmation of the marker pose.

Unassigned/ambiguous measured points retain their raw XYZ, without invented
marker or sphere IDs. Missing or invalid observations have no XYZ values.
Missing rows can be named only while their marker instance is recognised;
an entirely unrecognised marker cannot be assigned a tracking identity.
No image-frame row means no assertion about marker visibility for that time.
`visible=1` denotes a valid measured observation, not independent proof of
physical visibility of a named sphere. `not_in_marker_presence_mask` means the
SDK did not include that sphere in its marker fit; this alone does not prove
occlusion or a camera acquisition gap.

A bounded background queue isolates disk output from acquisition. The final
summary records actual exported frame/row counts, queue losses, definition
hashes, definition errors and write/finalization failures. `complete` refers
only to successful delivery of the received image frames, not to all physical
camera exposures or high-rate pose samples. Always inspect this summary before
analysing fiducial trajectories. No hardware synchronisation is introduced.
