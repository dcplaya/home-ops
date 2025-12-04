```
ceph fs flag set enable_multiple true --yes-i-really-mean-it
ceph osd pool create recovery 16 replicated ceph-filesystem-metadata_host_nvme
ceph fs new recovery-fs recovery ceph-filesystem-ec-4-2 --allow-dangerous-metadata-overlay
cephfs-data-scan init --force-init --filesystem recovery-fs --alternate-pool recovery
ceph fs reset recovery-fs --yes-i-really-mean-it
cephfs-table-tool recovery-fs:all reset session
cephfs-table-tool recovery-fs:all reset snap
cephfs-table-tool recovery-fs:all reset inode


cephfs-data-scan scan_extents --alternate-pool recovery --filesystem ceph-filesystem ceph-filesystem-ec-4-2
cephfs-data-scan scan_inodes --alternate-pool recovery --filesystem ceph-filesystem --force-corrupt --force-init ceph-filesystem-ec-4-2
cephfs-data-scan scan_links --filesystem recovery-fs