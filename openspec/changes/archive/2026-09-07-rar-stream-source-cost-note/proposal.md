# RAR stream-source disk copy cost note

Handbook §10 #6 layer 1 (P11): when a RAR is opened from a non-path stream,
`CostReceipt.notes` carries an open-time caveat that a compressed member read will
copy the archive to disk for RARLAB `unrar` or `rar`. Path sources unchanged. The
note is a static prediction, not a post-copy occurrence log.
