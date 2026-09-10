This is the exact canonical `chapter-verification/1` body retained from the
September 10, 2026 local default-brief dogfood run
`3a8358ad-f099-4120-b7ac-9f7a38ff99cd`, artifact
`01a08b4f-02e9-709f-a9ed-424ee9516b93`. SHA-256:
`5732a8484719cdf657e91bff94c1d601bc5d3be8419d0ad1910f050b8e5fd768`.

GLM 5.3 Flash through the qualified Baseten route generated the V2 verdict after
four kept sections rendered. The production artifact writer also retained a V1
status/reasons projection. The first export failed because the portable reader
rejected the added `editorial` field; this fixture prevents that regression and
checks that neither the full judgment nor its original hash is lost.

The verdict is a retained model judgment, not gold truth. Its first finding names
a real detector/word-alignment disagreement at 131.640 seconds; its second
incorrectly describes two separate detector intervals as continuous speech across
238.080 seconds. These tests verify the portable schema and exact provenance,
not the semantic accuracy of that freeform assertion. The run remains unaccepted.
