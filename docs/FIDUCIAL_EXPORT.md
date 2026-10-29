# Measured fiducial export

## Joint measurement channel (new native bridge)

The matching native bridge supplies a separate `ATRMEAS1` measurement ring,
independent of the legacy image channel. Each committed sample contains marker
poses, measured triangulated fiducials, original SDK correspondences, frame
counter and the same device/host timestamps as the pose sample. The legacy v2
layout remains unchanged. Read-only consumers reject incompatible layouts,
changed producer identities, torn slots and SDK reconnections. Ring overwrites
and incomplete/truncated SDK samples are reported explicitly.

Pixel retrieval is requested on a separate preallocated SDK query only for the
preview cadence, normally 20 Hz. The measurement query does not request pixels.
Both queries run on ONE SDK connection; a second camera client is never opened.
335 Hz is a target/native publication limit, not a guarantee of actual device,
SDK processing or disk throughput. Original timestamps and missing observations
remain unchanged. The application displays the measurement publication rate
separately from the actual stored recording rate and preview rate.

With this channel, fiducials are exported for exactly the accepted recording
frames, after the recorder's single existing frequency selection. Set recording
frequency to 335 Hz to request a 335 Hz recording, or select a lower rate for
both poses and fiducials. An SDK correspondence supplies the geometry ID,
tracking ID and 1-based sphere number; no nearest-predicted-point matching is
used on this path. Multiple conflicting SDK assignments remain unassigned.
The summary has `source=joint_measurement_ring_v1` and
`sdk_correspondence_exported=true`. Selected pose/fiducial frame counters and
device timestamps match exactly. No extra sensor hardware synchronisation is
asserted. CSV row counts differ when frames contain missing or unassigned points.

The fast channel requires the matching separately built native executable.
Source-only GitHub updates preserve a machine's native binary, so an older
bridge continues with the explicitly identified fallback below. SDKs, vendor
libraries, calibrations and real measurements must stay out of the repository.

## Legacy v2 image-channel fallback

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
