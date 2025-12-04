# Recover rook ceph after catastrophic monitor store loss

The OS SSD died on my kubernetes/rook/ceph cluster which left the cluster with
catastrophic monitor store loss. The process for recovering from just OSDs is
not well documented, especially not when combined with rook and cephfs with
multiple data pools. The last of which may never have been done before.

I am writing this document after the fact, so I may be missing some things, or
they may be out of order. I will try my best though as I think it could be
helpful.

## Table of contents

- [Recover rook ceph after catastrophic monitor store loss](#recover-rook-ceph-after-catastrophic-monitor-store-loss)
  - [Supplementary documentation](#supplementary-documentation)
  - [Prerequisites](#prerequisites)
  - [Recover the Ceph cluster](#recover-the-ceph-cluster)
    - [Find the original fsid](#find-the-original-fsid)
    - [Restore the fsid](#restore-the-fsid)
    - [Rebuild the mon store](#rebuild-the-mon-store)
    - [Replace the mon store with the rebuilt mon store](#replace-the-mon-store-with-the-rebuilt-mon-store)
    - [Update the mon map](#update-the-mon-map)
      - [Extract the mon map](#extract-the-mon-map)
      - [Remove superfluous mons (if applicable)](#remove-superfluous-mons-if-applicable)
      - [Update the fsid (again)](#update-the-fsid-again)
      - [Change the mon address](#change-the-mon-address)
      - [Commit the new mon map](#commit-the-new-mon-map)
      - [Restart the operator and mon](#restart-the-operator-and-mon)
      - [Update the rook config](#update-the-rook-config)
    - [Update OSD auth caps](#update-osd-auth-caps)
    - [Restore mon quorum](#restore-mon-quorum)
  - [Restore CephFS](#restore-cephfs)
  - [Identify CSI volumes and create static PVs](#identify-csi-volumes-and-create-static-pvs)
    - [List all RBD CSI volumes](#list-all-rbd-csi-volumes)
    - [Inspect RBD CSI volumes](#inspect-rbd-csi-volumes)
    - [List all CephFS CSI volumes](#list-all-cephfs-csi-volumes)
    - [Create static PVs](#create-static-pvs)
    - [RBD](#rbd)
    - [CephFS](#cephfs)
  - [Takeaways](#takeaways)

## Supplementary documentation

- [Recovery using OSDs](https://docs.ceph.com/en/reef/rados/troubleshooting/troubleshooting-mon/#recovery-using-osds)
- [Recover FS after mon store loss](https://docs.ceph.com/en/reef/cephfs/recover-fs-after-mon-store-loss/)
- [Rook disaster recovery](https://rook.io/docs/rook/latest-release/Troubleshooting/disaster-recovery/)

## Prerequisites

Reinstall Kubernetes and Rook. I used a newer version of Kubernetes, but kept
the Rook and Ceph versions the same.

Ensure Rook is configured to only use one mon when creating the Ceph cluster as
mon data must be restored to a single mon, and quorum reestablished from the
single mon.

## Recover the Ceph cluster

To recover a Ceph cluster from nothing but OSDs, we need to rebuild the monitor
store, find the original fsid and import it into a new Ceph cluster. It's a bit
tedious, but doable.

### Find the original fsid

Hopefully Rook will have recreated the deployments for the OSDs, and assuming
that is the case we can 'debug' one of the OSD deployments to find the original
fsid.

```sh
❯ kubectl rook-ceph maintenance start rook-ceph-osd-0
024/10/21 23:07:53 maxprocs: Updating GOMAXPROCS=1: using minimum allowed GOMAXPROCS
Info: fetching the deployment rook-ceph-osd-0 to be running

Info: deployment rook-ceph-osd-0 exists

Info: setting debug command to main container
Info: deployment rook-ceph-osd-0 scaled down

Info: waiting for the deployment pod rook-ceph-osd-0-57c9766f64-rtphk to be deleted

Info: ensure the debug deployment rook-ceph-osd-0 is scaled up

Info: pod rook-ceph-osd-0-57c9766f64-rtphk is ready for debugging
❯ kubectl exec deploy/rook-ceph-osd-0-maintenance -- cat /var/lib/ceph/osd/ceph-0/ceph_fsid
25b8bcaa-f3db-48b4-bca2-7b7531843629
# or ...
❯ kubectl exec deploy/rook-ceph-osd-0-maintenance -- ceph-bluestore-tool show-label --dev /dev/nvme0n1
```

If not, a live ISO of Ubuntu may be required. I started off with this, and
concluded it was a waste of time. The only thing which it was helpful for was
obtaining the original fsid and reassurance that recovery may be possible.

Assuming a live ISO of Ubuntu, install Ceph and get the fsid from one of the
OSDs.

```sh
❯ apt install -y ceph ceph-common ceph-osd
❯ sudo ceph-bluestore-tool show-label --dev /dev/nvme0n1
```

### Restore the fsid

The new Rook Ceph cluster will have a new fsid, which is useless and needs to be
replaced with the original fsid.

Where `25b8bcaa-f3db-48b4-bca2-7b7531843629` is the original fsid:

```sh
kubectl patch secret rook-ceph-mon -p '{
        "stringData": {
                "fsid": "25b8bcaa-f3db-48b4-bca2-7b7531843629"
        }
}'
```

Rook should eventually reconcile and create the OSD deployments if it hasn't
already, and propagate the new fsid to
`/var/lib/rook/rook-ceph/rook-ceph.config`.

### Rebuild the mon store

The new mon store needs to be updated by each OSD. This is easy on a single
node, and a bit annoying on multiple nodes. This is written for recovery of a
single node, but can be extrapolated for multiple nodes.

Essentially:

```sh
❯ kubectl rook-ceph maintenance start rook-ceph-osd-0
❯ kubectl exec -it deploy/rook-ceph-osd-0-maintenance -- bash
Defaulted container "osd" out of: osd, log-collector, activate (init), expand-bluefs (init), chown-container-data-dir (init)
[root@rook-ceph-osd-0-debug-695fd4c7b-c4w9q ceph]# mkdir /tmp/monstore/
[root@rook-ceph-osd-0-debug-695fd4c7b-c4w9q ceph]# ceph-objectstore-tool --type bluestore --data-path /var/lib/ceph/osd/ceph-0 --op update-mon-db --mon-store-path /tmp/monstore --no-mon-config
osd.0   : 0 osdmaps trimmed, 653 osdmaps added.
❯ kubectl rook-ceph debug stop rook-ceph-osd-0
```

This can be automated with the following script. Unfortunately, the data that is exported must be manually copied between each node that has OSDs. My OSDs aren't in "order", as in OSD 0, 1, 2 are all on different nodes. The script below is left for reference but will not work for my setup.

Go ahead and start a maintenance pod for each OSD. FOr me, my maintenance pod would `Init:Error` but then eventually start. It isnt a fast process, so give it some time.
```sh
for osd in $(kubectl get deploy -lapp=rook-ceph-osd -ojsonpath='{.items[*].metadata.labels.osd}'); do
        kubectl rook-ceph maintenance start "rook-ceph-osd-$osd"
done
```

I then saved each monmap to my `/var/lib/rook/monstore-recovery` folder on the first node. I have a seperate debug daemonset running with a simple ubuntu image that allows me to SCP files across the debug pods using the pod network IP addresses

The script below is left for reference but will not work for my setup.
```sh
# make sure to `mkdir -p /var/lib/rook/monstore-recovery` before running

for osd in $(kubectl get deploy -lapp=rook-ceph-osd -ojsonpath='{.items[*].metadata.labels.osd}'); do
        kubectl rook-ceph maintenance start "rook-ceph-osd-$osd"
        kubectl exec "deploy/rook-ceph-osd-$osd-debug" -- ceph-objectstore-tool --type bluestore --data-path "/var/lib/ceph/osd/ceph-$osd" --op update-mon-db --mon-store-path /var/lib/rook/monstore-recovery --no-mon-config
        kubectl rook-ceph maintenance stop "rook-ceph-osd-$osd"
done
```

Once the mon db has been updated, it can then be rebuilt.

```sh
❯ kubectl rook-ceph maintenance start rook-ceph-osd-0
❯ kubectl exec deploy/rook-ceph-osd-0-maintenance -- ceph-monstore-tool /var/lib/rook/monstore-recovery rebuild -- --keyring /var/lib/rook/rook-ceph/client.admin.keyring
❯ kubectl rook-ceph debug stop rook-ceph-osd-0
```

### Replace the mon store with the rebuilt mon store

Once the mon store has been rebuilt, the empty mon can be replaced with the
rebuilt mon store.

```sh
❯ kubectl rook-ceph maintenance start rook-ceph-osd-0
❯ kubectl exec deploy/rook-ceph-osd-0-maintenance -- cp -r /var/lib/rook/mon-a /var/lib/rook/mon-a-backup
❯ kubectl exec deploy/rook-ceph-osd-0-maintenance -- rm -rf /var/lib/rook/mon-a/data/store.db
❯ kubectl exec deploy/rook-ceph-osd-0-maintenance -- cp -r /var/lib/rook/monstore-recovery/store.db/ /var/lib/rook/mon-a/data/store.db
❯ kubectl rook-ceph debug stop rook-ceph-osd-0
```

### Update the mon map

Start a debug session with any OSD.

```sh
❯ kubectl rook-ceph maintenance start rook-ceph-osd-0
```

Scale down the operator and the mon.

```sh
❯ kubectl scale deploy/rook-ceph-operator --replicas=0
deployment.apps/rook-ceph-operator scaled
❯ kubectl scale deploy/rook-ceph-mon-a --replicas=0
deployment.apps/rook-ceph-mon-a scaled
```

Update the mon map from the OSD debug session. There should only be one mon in
the mon map, but if there are more, they can be removed.

#### Extract the mon map

```sh
❯ kubectl exec deploy/rook-ceph-osd-0-maintenance -- ceph-mon -i a --extract-monmap /tmp/monmap --mon-data /var/lib/rook/mon-a/data
❯ kubectl exec deploy/rook-ceph-osd-0-maintenance -- monmaptool /tmp/monmap --print
monmaptool: monmap file /tmp/monmap
epoch 0
fsid 00000000-0000-0000-0000-000000000000
last_changed 2024-10-21T23:57:38.363259+0000
created 2024-10-21T23:57:38.363259+0000
min_mon_release 0 (unknown)
election_strategy: 1
0: v2:10.99.179.26:3300/0 mon.a
1: v2:10.104.57.166:3300/0 mon.b
2: v2:10.105.210.150:3300/0 mon.c
```

#### Remove superfluous mons (if applicable)

Remove superfluous mons if they exist (they shouldn't, but they did in my case
because I hadn't configured rook to use only a single mon).

```sh
❯ kubectl exec deploy/rook-ceph-osd-0-maintenance -- monmaptool /tmp/monmap --rm b
❯ kubectl exec deploy/rook-ceph-osd-0-maintenance -- monmaptool /tmp/monmap --rm c
```

#### Update the fsid (again)

Where `51ea9bae-86ce-4f9f-b15e-1fa7533b3508` is your fsid.

```sh
❯ kubectl exec deploy/rook-ceph-osd-0-maintenance -- monmaptool /tmp/monmap --fsid 51ea9bae-86ce-4f9f-b15e-1fa7533b3508
```

#### Change the mon address

Get the new mon address from Kubernetes.

```sh
❯ kubectl get svc rook-ceph-mon-a -o jsonpath='{.spec.clusterIP}'
10.104.57.166
```

Where `10.104.57.166` is the IP of the new mon.

```sh
❯ kubectl exec deploy/rook-ceph-osd-0-maintenance -- monmaptool /tmp/monmap --rm a
❯ kubectl exec deploy/rook-ceph-osd-0-maintenance -- monmaptool /tmp/monmap --add a 10.104.57.166:3300
```

#### Commit the new mon map

Print the new mon map to ensure it is correct, and then commit it.

```sh
❯ kubectl exec deploy/rook-ceph-osd-0-maintenance -- monmaptool /tmp/monmap --print
monmaptool: monmap file /tmp/monmap
epoch 1
fsid 2a7f4468-5f3b-4063-9bea-24026ea40b4c
last_changed 2024-10-21T23:57:38.363259+0000
created 2024-10-21T23:57:38.363259+0000
min_mon_release 0 (unknown)
election_strategy: 1
0: v2:10.104.57.166:3300/0 mon.a
❯ kubectl exec deploy/rook-ceph-osd-0-maintenance -- ceph-mon -i a --inject-monmap /tmp/monmap --mon-data /var/lib/rook/mon-a/data
❯ kubectl rook-ceph debug stop rook-ceph-osd-0
```

#### Restart the operator and mon

```sh
❯ kubectl scale deploy/rook-ceph-operator --replicas=1
deployment.apps/rook-ceph-operator scaled
❯ kubectl scale deploy/rook-ceph-mon-a --replicas=1
deployment.apps/rook-ceph-mon-a scaled
```

#### Update the rook config

This step should not be necessary if Rook was correctly configured to use only a
single mon, but I didn't do that and had to manually update the mon config.

I saw vague errors like this, and it was because it couldn't establish a quorum.

```
CEPH cluster became unresponsive: e5 handle_auth_request failed to assign global_id
```

```sh
❯ mon_host=$(kubectl get svc rook-ceph-mon-a -o jsonpath='{.spec.clusterIP}')
kubectl patch secret rook-ceph-config -p '{"stringData": {"mon_host": "[v2:'"${mon_host}"':3300]", "mon_initial_members": "a"}}'
```

In addition, the mons may need to be removed from the mon endpoints configmap.

```sh
❯ mon_host=$(kubectl get svc rook-ceph-mon-a -o jsonpath='{.spec.clusterIP}')
kubectl patch cm rook-ceph-mon-endpoints -p '{"data": {"data": "a='"${mon_host}"':3300"}}'
```

### Update OSD auth caps

The OSDs don't have the `mgr` cap, and it needs to be added manually.

```text
Error EINVAL: key for osd.14 exists but cap mgr does not match
```

The caps can be added in the toolbox pod with:

```sh
❯ ceph auth caps osd.14 mon 'allow profile osd' mgr 'allow profile osd' osd 'allow *'
```

To save time, this script will do it for all OSDs:

```sh
for osd in $(kubectl get deploy -lapp=rook-ceph-osd -ojsonpath='{.items[*].metadata.labels.osd}'); do
        kubectl exec deploy/rook-ceph-tools -- ceph auth caps $osd mon 'allow profile osd' mgr 'allow profile osd' osd 'allow *'
done
```

### Restore mon quorum

At this point, the mon should be active and the Ceph cluster should be
"healthy".

Increase the number of mons from 1 to 3 in the CephCluster custom resource.

Then, the mons should join and establish quorum.

If not, we can follow the guide for [restoring mon quorum](https://rook.io/docs/rook/latest-release/Troubleshooting/disaster-recovery/#restoring-mon-quorum).

```sh
❯ kubectl rook-ceph mons restore-quorum a
```

## Restore CephFS

The guidance for [restoring CephFS](https://docs.ceph.com/en/reef/cephfs/recover-fs-after-mon-store-loss/)
with a single data pool should mostly just work.

Where `main` is the name of the Ceph filesystem.

```sh
❯ kubectl exec deploy/rook-ceph-tools -- ceph fs new main main-metadata main-default
```

If, like in my case, there are multiple data pools, or erasure coding is used,
then we have to do some hacks.

The existing data pools cannot be added to the Ceph filesystem, because it
thinks it's already part of the filesystem, even though it isn't. This can be
resolved by removing the application association with the data pool. The docs
suggest there could be data loss, but my understanding is that it's simply
changing some metadata. There is also no other way to do this, so.

```sh
❯ kubectl exec deploy/rook-ceph-tools -- ceph fs ls
name: main, metadata pool: main-metadata, data pools: [main-default ]
❯ kubectl exec deploy/rook-ceph-tools -- ceph fs add_data_pool main main-nvme-ec
Error EINVAL: RADOS pool 'main-nvme-ec' is already used by filesystem 'main' as a 'data' pool for application 'cephfs'
```

```sh
❯ kubectl exec deploy/rook-ceph-tools -- ceph osd pool application get main-nvme-ec {
        "cephfs": {
                "data": "main"
        }
}
❯ kubectl exec deploy/rook-ceph-tools -- ceph osd pool application rm main-nvme-ec cephfs data
removed application 'cephfs' key 'data' on pool 'main-nvme-ec'
❯ kubectl exec deploy/rook-ceph-tools -- ceph fs add_data_pool ceph-filesystem ceph-filesystem-ec-4-2
Pool 'main-nvme-ec' (id '41') has pg autoscale mode 'on' but is not marked as bulk.
Consider setting the flag by running # ceph osd pool set main-nvme-ec bulk true
added data pool 41 to fsmap
```

Other than that, the rest of the instructions should work.

```sh
❯ kubectl exec deploy/rook-ceph-tools -- ceph fs set main joinable true
main marked joinable; MDS may join as newly active.
```

The MDS pods may need to be restarted, but otherwise the Ceph filesystem should
now be healthy.

```sh
❯ kubectl exec deploy/rook-ceph-tools -- ceph fs status
main - 1 clients ====
RANK STATE MDS ACTIVITY DNS INOS DIRS CAPS
0 active main-b Reqs: 0 /s 0 0 0 0
0-s standby-replay main-a Evts: 0 /s 0 0 0 0
POOL TYPE USED AVAIL
main-metadata metadata 765M 3811G
main-default data 24.0k 3811G
main-nvme-ec data 6684G 7622G
MDS version: ceph version 18.2.2 (531c0d11a1c5d39fbfe6aa8a521f023abf3bf3e2) reef (stable)
```

I have opened [an issue](https://tracker.ceph.com/issues/68682) on the tracker
to officially document, or support restoring Ceph filesystems with multiple data
pools.

## Identify CSI volumes and create static PVs

This bit is painful. The mapping of CSI volumes to persistent volumes is lost if
the Kubernetes control plane is lost. This means that each CSI volume has to be
inspected manually and identified. I recommend a spreadsheet for this.

Some CSI volumes are pretty easy to identify just by their size, but the rest
really do need manual inspection. There will be a lot of CSI volumes which are
either unused, duplicates, invalid or just left over from a 'retain' reclaim
policy.

### List all RBD CSI volumes

```sh
❯ kubectl exec deploy/rook-ceph-tools -- rbd ls ceph-blockpool-nvme
...
```

If the size of the CSI volume is useful, I wrote this script to list the volumes
and their sizes:

```sh
for image in $(rbd ls ceph-blockpool-nvme); do

echo -n "$image,"

# extract "250 GiB" from "size 250 GiB in 64000 objects"

rbd info ceph-blockpool-nvme/$image | grep size | awk '{print $2, $3}' | tr -d ' ' | tr -d 'B' | numfmt --from=auto --to-unit=G

done
```

### Inspect RBD CSI volumes

Follow the guide for creating the [direct mount pod](https://rook.io/docs/rook/v1.15/Troubleshooting/direct-tools/).

The direct mount pod can be used to mount RBD images and inspect them quickly.
In my case, I had about 60 RBD images to inspect.

Map, mount and list the contents of the RBD image:

```sh
kubectl exec --namespace rook-ceph -it deploy/rook-direct-mount -- rbd map ceph-blockpool-nvme/csi-vol-0830b37f-1ee4-4f41-8e2f-f9bbee6c90d6
kubectl exec --namespace rook-ceph -it deploy/rook-direct-mount -- mount /dev/rbd0 /mnt
kubectl exec --namespace rook-ceph -it deploy/rook-direct-mount -- ls -al /mnt
kubectl exec --namespace rook-ceph -it deploy/rook-direct-mount -- umount /mnt
kubectl exec --namespace rook-ceph -it deploy/rook-direct-mount -- rbd unmap /dev/rbd0
```

Unmount and unmap the RBD image:

```sh
kubectl exec --namespace rook-ceph -it deploy/rook-direct-mount -- umount /mnt
kubectl exec --namespace rook-ceph -it deploy/rook-direct-mount -- rbd unmap /dev/rbd0
```

A script which templates all the commands may be helpful to make this go a bit
quicker.

```js
console.log(`
csi-vol-10fb6512-7888-4416-bac4-810d5623e542
csi-vol-47e9037c-2187-4272-bec9-5ac0b69479f7
csi-vol-498f8772-43fe-4a7f-8676-d4c054b2b006
csi-vol-89b44878-6472-4b2b-a172-ec3eb708f399
csi-vol-aeed2194-ad77-41b0-95be-3bc625b2afa0
csi-vol-505470ff-002c-44cc-9e33-03f3c622720b
csi-vol-54b3fe0e-eb75-45a5-80dd-8957d40fecc3
csi-vol-702f5574-61ab-42ad-ac66-df3c7d160cdd
csi-vol-994b927b-379a-40c4-9c8c-3477cbe21a4d
csi-vol-b5ec04d9-5fc4-4cba-960b-08855921fc63
csi-vol-d923cd76-ad32-4862-9dbb-be33064a71a0
csi-vol-f61d70ad-2eb4-4b25-8c96-63226bb87811
`.trim().split('\n').map(v => `
# inspect ${v}

kubectl exec --namespace rook-ceph -it deploy/rook-direct-mount -- rbd map replicapool-nvme/${v}

kubectl exec --namespace rook-ceph -it deploy/rook-direct-mount -- mount /dev/rbd0 /mnt

kubectl exec --namespace rook-ceph -it deploy/rook-direct-mount -- ls -al /mnt

# cleanup ${v}

kubectl exec --namespace rook-ceph -it deploy/rook-direct-mount -- umount /mnt

kubectl exec --namespace rook-ceph -it deploy/rook-direct-mount -- rbd unmap /dev/rbd0
`.trim()).join('\n\n'))
```

### List all CephFS CSI volumes

Where `main` is the name of the Ceph filesystem.

```sh
❯ kubectl exec deploy/rook-ceph-tools -- ceph fs subvolume ls main csi
[
    {
        "name": "csi-vol-95d94b9d-e3f7-4b95-bd79-6005576ee3c1"
    },
    {
        "name": "csi-vol-deea4df5-96ec-4a3c-b452-36a9ab2b9c1f"
    }
]
```

Truthfully, I used the Ceph dashboard to inspect the Ceph filesystems, but it is
also possible to do from the command line.

```sh
❯ kubectl exec deploy/rook-ceph-tools -- ceph fs subvolume ls main csi/csi-vol-95d94b9d-e3f7-4b95-bd79-6005576ee3c1
[
    {
        "name": "45c65a90-bd64-46d9-aaf0-80d229aeb994"
    }
]
❯ kubectl exec deploy/rook-ceph-tools -- bash-4.4$ ceph fs subvolume ls main csi/csi-vol-95d94b9d-e3f7-4b95-bd79-6005576ee3c1/45c65a90-bd64-46d9-aaf0-80d229aeb994
[
    {
        "name": "shows"
    },
    {
        "name": "books"
    },
    {
        "name": "music"
    },
    {
        "name": "downloads"
    },
    {
        "name": "movies"
    }
]
```

### Create static PVs

This bit is also a bit painful, but workable.

The documentation for [creating static PVs](https://github.com/ceph/ceph-csi/blob/52fcb7b00c3d586c12c05593b1c73ad2c42b171f/docs/static-pvc.md)
is helpful, but hopefully this document is better as it caters directly to Rook.

Instead of using the `volumeName` attribute in stateful sets, I'd suggest just
creating the PVCs manually. It's a shame, but there is no other way around it if
the stateful set has multiple replicas. Even if it only has one replica, I feel
it's probably cleaner and more portable to just provision the PVCs manually.
When doing this, ensure the PVC is created before the stateful set to ensure it
doesn't create a new PVC.

### RBD

For each volume, ensure the following:

- Storage requests must match.
- The `volumeName` attribute on the PVC must match the name of the PV.
- The `volumeHandle` attribute must match the name of the RBD image.

Other than that, this should "just work".

```yaml
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: config-jellyfin-0
  namespace: media
spec:
  accessModes:
  - ReadWriteOnce
  resources:
    requests:
      storage: 1Gi
  storageClassName: rook-ceph-nvme
  volumeName: media-config-jellyfin-0
---
apiVersion: v1
kind: PersistentVolume
metadata:
  name: media-config-jellyfin-0
spec:
  accessModes:
  - ReadWriteOnce
  capacity:
    storage: 1Gi
  csi:
    controllerExpandSecretRef:
      name: rook-csi-rbd-provisioner
      namespace: rook-ceph
    driver: rook-ceph.rbd.csi.ceph.com
    fsType: ext4
    nodeStageSecretRef:
      name: rook-csi-rbd-node
      namespace: rook-ceph
    volumeAttributes:
      clusterID: rook-ceph
      imageFeatures: layering,fast-diff,object-map,deep-flatten,exclusive-lock
      pool: replicapool-nvme
      staticVolume: "true"
    volumeHandle: csi-vol-4d763654-d39f-41ac-b4e2-12024c2e297e
  mountOptions:
  - discard
  persistentVolumeReclaimPolicy: Retain
  storageClassName: rook-ceph-nvme
  volumeMode: Filesystem
```

### CephFS

CephFS is much the same as RBD, except it needs some credentials to be formatted
differently. This is explain in the [CephFS shared volume](https://rook.io/docs/rook/latest-release/Storage-Configuration/Shared-Filesystem-CephFS/filesystem-storage/#shared-volume-creation)
docs.

```sh
❯ kubectl get secret rook-csi-cephfs-node -oyaml > rook-csi-cephfs-node.yaml
```

Change the name of the object to `rook-csi-cephfs-node-user`, and change the
keys from `adminID` and `adminKey` to `userID` and `userKey`, then create the
new object.

```sh
❯ kubectl apply -f rook-csi-cephfs-node.yaml
```

Like before, ensure the following:

- Storage requests must match.
- The `volumeName` attribute on the PVC must match the name of the PV.
- The `volumeHandle` attribute can be anything, either use the name of the PV or
  the CSI volume.
- The `rootPath` attribute must match the path of the CephFS subvolume.

```yaml
---
apiVersion: v1
kind: PersistentVolumeClaim
metadata:
  name: media
  namespace: media
spec:
  accessModes:
  - ReadWriteMany
  resources:
    requests:
      storage: 8Ti
  storageClassName: rook-cephfs-nvme
  volumeMode: Filesystem
  volumeName: media-media
---
apiVersion: v1
kind: PersistentVolume
metadata:
  name: media-media
spec:
  accessModes:
  - ReadWriteMany
  capacity:
    storage: 8Ti
  csi:
    controllerExpandSecretRef:
      name: rook-csi-cephfs-provisioner
      namespace: rook-ceph
    driver: rook-ceph.cephfs.csi.ceph.com
    nodeStageSecretRef:
      name: rook-csi-cephfs-node-user
      namespace: rook-ceph
    volumeAttributes:
      clusterID: rook-ceph
      fsName: main
      rootPath: /volumes/csi/csi-vol-95d94b9d-e3f7-4b95-bd79-6005576ee3c1/45c65a90-bd64-46d9-aaf0-80d229aeb994
      staticVolume: "true"
    volumeHandle: media-media
  persistentVolumeReclaimPolicy: Retain
  storageClassName: rook-cephfs-nvme
  volumeMode: Filesystem
```

I did encounter this error when creating the CephFS volumes because I passed it
the 'discard' mount option. It didn't like that.

```text
stderr: unable to get monitor info from DNS SRV with service name: ceph-mon
2023-01-09T17:51:39.771+0000 7f9a29f55f40 -1 failed for service _ceph-mon._tcp
```

## Takeaways

- Use enterprise SSDs.
- Backup important data.
- Distribute monitors across multiple machines if possible.
- Backup, or actively mirror (raid 1) the OS SSD to prevent the loss of etcd
  data, rook data and mon data.
- Ceph is surprisingly resilient, even if the restoration process is hard and
  convoluted.