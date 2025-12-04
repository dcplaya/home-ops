import subprocess
import json
import shlex
import sys
import logging
import time
import os
import shutil

from kubernetes import client, config
from kubernetes.stream import stream

def setup_logger():
    logger = logging.getLogger("recover_mon_map_from_osds")
    logger.setLevel(logging.DEBUG)
    ch = logging.StreamHandler(sys.stdout)
    ch.setLevel(logging.DEBUG)
    formatter = logging.Formatter('%(asctime)s - %(name)s - %(levelname)s - %(message)s')
    ch.setFormatter(formatter)
    logger.addHandler(ch)
    return logger

logger = setup_logger()

def load_config(path_to_config = None):
    logger.info("Loading Kubernetes configuration")
    config.load_kube_config(path_to_config)
    v1 = client.CoreV1Api()
    return v1

def get_pods(v1, namespace = "rook-ceph", label_selector = ""):
    logger.info(f"Getting pods in namespace '{namespace}' with label selector '{label_selector}'")
    pods = v1.list_namespaced_pod(namespace, label_selector=label_selector, )
    return pods.items

def get_osd_node_pairs(osd_pods):
    osd_node_pairs = []
    for pod in osd_pods:
        osd_id = None
        for env in pod.spec.containers[0].env:
            if env.name == "ROOK_OSD_ID":
                osd_id = env.value
                break
        if osd_id is not None:
            osd_node_pairs.append((osd_id, pod.spec.node_name, pod.metadata.name))
            logger.info(f"Found OSD ID {osd_id} on node {pod.spec.node_name}")
        else:
            logger.warning(f"Could not find OSD ID for pod {pod.metadata.name}")
    return osd_node_pairs

def is_maintenance_pod_running(v1,osd_number):
    label_selector = f"app=rook-ceph-osd,osd={osd_number}"
    pods = get_pods(v1, namespace="rook-ceph", label_selector=label_selector)
    for pod in pods:
        if "maintenance" in pod.metadata.name and pod.status.phase == "Running":
            return True
    return False

def start_osd_maintenance_pod(v1,osd_number):
    # check if the maintenance pod is already running
    if is_maintenance_pod_running(v1,osd_number):
        logger.info(f"Maintenance pod for OSD {osd_number} is already running")
        return
    else:
        logger.info(f"Starting maintenance pod for OSD {osd_number}")
        command = ['kubectl', 'rook-ceph', 'maintenance', 'start', f'rook-ceph-osd-{osd_number}']

    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True
        )
        logger.info(f"Maintenance pod started: {result.stdout}")

    except subprocess.CalledProcessError as e:
        logger.error(f"Error starting maintenance pod: {e.stderr}")

def is_mon_maintenance_pod_running(v1,mon_letter):
    label_selector = f"app=rook-ceph-mon,mon={mon_letter}"
    pods = get_pods(v1, namespace="rook-ceph", label_selector=label_selector)
    for pod in pods:
        if "maintenance" in pod.metadata.name and pod.status.phase == "Running":
            return True
    return False

def start_mon_maintenance_pod(v1,mon_pod_name, namespace="rook-ceph"):
    logger.info(f"Starting maintenance pod for monitor pod {mon_pod_name}")
    pods = get_pods(v1, namespace, f"app=rook-ceph-mon")
    # get mon deployment name
    mon_letter = pods[0].metadata.labels['mon']
    if is_mon_maintenance_pod_running(v1,f'{mon_letter}'):
        logger.info(f"Maintenance pod for monitor pod rook-ceph-mon-{mon_letter}-maintenance is already running")
        return
    command = ['kubectl', 'rook-ceph', 'maintenance', 'start', f'rook-ceph-mon-{mon_letter}']
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True
        )
        logger.info(f"Maintenance pod started for monitor pod {mon_pod_name}: {result.stdout}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Error starting maintenance pod for monitor pod {mon_pod_name}: {e.stderr}")
        sys.exit(0)

def copy_path_from_pod(v1, pod_name, source_path, destination_path, namespace="rook-ceph"):
    logger.info(f"Copying path {source_path} from pod {pod_name} to local path {destination_path}")
    command = ['kubectl', 'cp', f'--namespace', f'{namespace}', f'{pod_name}:{source_path}', f'{destination_path}']
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True
        )
        logger.info(f"Path {source_path} copied successfully from pod {pod_name} to {destination_path}: {result.stdout}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Error copying path {source_path} from pod {pod_name}: {e.stderr}")
        sys.exit(0)

def copy_path_to_pod(v1, pod_name, source_path, destination_path,  namespace="rook-ceph"):
    logger.info(f"Copying path {source_path} to pod {pod_name} at {destination_path}")
    command = ['kubectl', 'cp', f'--namespace', f'{namespace}', f'{source_path}', f'{pod_name}:{destination_path}']
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True
        )
        logger.info(f"Path {source_path} copied successfully to pod {pod_name} at {destination_path}: {result.stdout}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Error copying path {source_path} to pod {pod_name}: {e.stderr}")
        sys.exit(0)

def delete_path_in_pods(v1, pod_name, path_to_delete, namespace="rook-ceph"):
    logger.info(f"Deleting path {path_to_delete} in pod {pod_name}")
    command = ['kubectl', 'exec', '-it', f'--namespace', f'{namespace}', f'{pod_name}', '--', 'rm', '-rf', f'{path_to_delete}']
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True
        )
        logger.info(f"Path {path_to_delete} deleted successfully in pod {pod_name}: {result.stdout}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Error deleting path {path_to_delete} in pod {pod_name}: {e.stderr}")
        sys.exit(0)

def create_path_in_pod(v1, pod_name, path_to_create, namespace="rook-ceph"):
    logger.info(f"Creating path {path_to_create} in pod {pod_name}")
    command = ['kubectl', 'exec', '-it', f'--namespace' ,f'{namespace}', f'{pod_name}', '--', 'mkdir', '-p', f'{path_to_create}']
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True
        )
        logger.info(f"Path {path_to_create} created successfully in pod {pod_name}: {result.stdout}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Error creating path {path_to_create} in pod {pod_name}: {e.stderr}")
        sys.exit(0)

def extract_monmap_from_osd(v1,
                            osd_number,
                            maintenance_pod_name,
                            path_to_monstore = "/var/lib/rook/monstore-recovery",
                            namespace="rook-ceph"):
    MAX_MONITOR_TIME = 300

    create_path_in_pod(v1 = v1,
                        pod_name = maintenance_pod_name,
                        path_to_create = path_to_monstore,
                        namespace = namespace)

    command = ['kubectl','exec', '-it', '--namespace', f'{namespace}', f'{maintenance_pod_name}', '--',
                'ceph-objectstore-tool', '--type', 'bluestore', '--data-path',
                f'/var/lib/ceph/osd/ceph-{osd_number}', '--op', 'update-mon-db',
                '--mon-store-path', f'{path_to_monstore}', '--no-mon-config']

    process = subprocess.Popen(command)
    start_time = time.time()

    while time.time() - start_time < MAX_MONITOR_TIME:
        return_code = process.poll()

        if return_code is not None:
            if return_code == 0:
                logger.info(f"Monmap extracted successfully from OSD {osd_number}")
            else:
                logger.error(f"Error extracting monmap from OSD {osd_number}, return code: {return_code}")
                sys.exit(0)
            return
        elapsed_time = time.time() - start_time
        sys.stderr.flush()
        logger.info(f"Monitoring... Time elapsed: {elapsed_time:.2f}s")
        time.sleep(5)

    if process.poll() is None:
        process.terminate()
        logger.error(f"Extraction process for OSD {osd_number} timed out after {MAX_MONITOR_TIME} seconds")
        try:
            process.wait(timeout=10)
            logger.info("Process terminated gracefully after timeout.")
        except subprocess.TimeoutExpired:
            process.kill()
            logger.info("Process killed after failing to terminate gracefully.")

def rebuild_monmap(maintenance_pod_name, path_to_monstore = "/var/lib/rook/monstore-recovery",namespace="rook-ceph"):
    logger.info(f"Rebuilding monmap in OSD {maintenance_pod_name}")
    command = ['kubectl', 'exec', '-it', '--namespace', f'{namespace}', f'{maintenance_pod_name}', '--',
                'ceph-monstore-tool', f'{path_to_monstore}', 'rebuild', '--',
                '--keyring', '/var/lib/rook/rook-ceph/client.admin.keyring']
    try:
        result = subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=True
        )
        logger.info(f"Monmap rebuilt successfully: {result.stdout}")
    except subprocess.CalledProcessError as e:
        logger.error(f"Error rebuilding monmap: {e.stderr}")
        sys.exit(0)




def main():

    cluster = load_config()
    namespace = "rook-ceph"
    label_selector = "name=ubuntu-debug"

    local_temp_dir = "/tmp/rook-ceph-monmap-recovery"
    local_path = "/tmp/monstore-recovery"
    path_to_monstore = "/var/lib/rook/monstore-recovery"

    ubunut_debug_pods = get_pods(cluster, namespace,label_selector)
    osd_pods = get_pods(cluster, namespace, "app=rook-ceph-osd")

    number_of_osds = len(osd_pods)
    logger.info(f"Found {number_of_osds} OSD pods")

    osd_pairs = get_osd_node_pairs(osd_pods)

    # Start maintenance pods for each OSD
    for number_of_osds in osd_pairs:
        start_osd_maintenance_pod(v1 = cluster,osd_number=number_of_osds[0])

    # Make sure the local path is empty
    command = f"rm -rf {local_path}/*"
    subprocess.run(shlex.split(command), check=True)

    # Delete local temp dir if exists recursively and recreate it
    if os.path.exists(local_temp_dir):
        shutil.rmtree(local_temp_dir)
    os.makedirs(local_temp_dir, exist_ok=True)
    if os.path.exists(local_path):
        shutil.rmtree(local_path)
    os.makedirs(local_path, exist_ok=True)

    # Extract monmap from each OSD and use the corresponding ubuntu-debug pod to transfer the data to build the monmap
    for index,number_of_nodes in enumerate(osd_pairs):
        namespace = "rook-ceph"
        osd_number = number_of_nodes[0]
        node_name = number_of_nodes[1]
        current_maintenace_pod_name = number_of_nodes[2]
        previous_maintenace_pod_name = None
        if index > 0:
            previous_maintenace_pod_name = osd_pairs[index - 1][2]
        logger.info(f"Processing OSD {osd_number} on node {node_name}")

        if index > 0:
            # Verify the current pod path is empty
            delete_path_in_pods(v1 = cluster,
                                pod_name = current_maintenace_pod_name,
                                path_to_delete = path_to_monstore,
                                namespace = namespace)
            # Copy the previous monstore data to local temp dir
            copy_path_from_pod(v1 = cluster,
                                pod_name = previous_maintenace_pod_name,
                                source_path = path_to_monstore,
                                destination_path = local_temp_dir,
                                namespace = namespace)
            # Delete the previous monstore data in the previous maintenance pod
            delete_path_in_pods(v1 = cluster,
                                pod_name = previous_maintenace_pod_name,
                                path_to_delete = path_to_monstore,
                                namespace = namespace)

            copy_path_to_pod(v1 = cluster,
                            pod_name = current_maintenace_pod_name,
                            source_path = local_temp_dir,
                            destination_path = path_to_monstore,
                            namespace = namespace)
        else:
            logger.info(f"Deleting any existing monstore data in the first maintenance pod for OSD {osd_number}")
            delete_path_in_pods(v1 = cluster,
                                pod_name = current_maintenace_pod_name,
                                path_to_delete = path_to_monstore,
                                namespace = namespace)
            create_path_in_pod(v1 = cluster,
                                pod_name = current_maintenace_pod_name,
                                path_to_create = path_to_monstore,
                                namespace = namespace)

        # Extract monmap from OSD
        extract_monmap_from_osd(v1 = cluster,
                                maintenance_pod_name = current_maintenace_pod_name,
                                osd_number=osd_number,
                                path_to_monstore = path_to_monstore)

    # Rebuild the monmap in the very last osd pair
    last_maintenace_pod_name = osd_pairs[-1][2]
    rebuild_monmap(maintenance_pod_name=last_maintenace_pod_name, path_to_monstore=path_to_monstore)

    # Grab the rebuild monmap
    copy_path_from_pod(v1 = cluster,
                        pod_name = last_maintenace_pod_name,
                        source_path = path_to_monstore,
                        destination_path = local_path,
                        namespace = namespace)

    # Get mon pod info
    mon_pods = get_pods(cluster, namespace, "app=rook-ceph-mon")
    if len(mon_pods) == 0:
        logger.error("No monitor pods found in the cluster")
        sys.exit(0)
    else:
        mon_pod_name = mon_pods[0].metadata.name
        mon_pod_node_name = mon_pods[0].spec.node_name
        logger.info(f"Monitor pod found: {mon_pod_name} on node {mon_pod_node_name}")

    # start a mon maintenance pod
    start_mon_maintenance_pod(v1 = cluster,
                            mon_pod_name = mon_pod_name,
                            namespace = namespace)
    # get mon maintenance pod name
    mon_maintenance_pod_name = get_pods(cluster, namespace, f"app=rook-ceph-mon")[0].metadata.name
    logger.info(f"Monitor maintenance pod name: {mon_maintenance_pod_name}")

    # Backup existing mon data on the mon pod
    mon_data_path = "/var/lib/ceph/mon/ceph-a"
    backup_mon_data_path = "/var/lib/ceph/mon/ceph-a-backup" + time.strftime("%Y%m%d-%H%M%S")
    logger.info(f"Backing up existing mon data from {mon_data_path} to {backup_mon_data_path} on pod {mon_maintenance_pod_name}")
    copy_path_from_pod(v1 = cluster,
                    pod_name = mon_maintenance_pod_name,
                    source_path = mon_data_path,
                    destination_path = "/tmp/mon-backup",
                    namespace = namespace)
    copy_path_to_pod(v1 = cluster,
                    pod_name = mon_maintenance_pod_name,
                    source_path = "/tmp/mon-backup",
                    destination_path = backup_mon_data_path,
                    namespace = namespace)

    # Copy the rebuilt monmap to the mon host via the maintenance pod
    mon_data_path = "/var/lib/ceph/mon/ceph-a"
    copy_path_to_pod(v1 = cluster,
                    pod_name = mon_pod_name,
                    source_path = local_path,
                    destination_path = mon_data_path,
                    namespace = namespace)


if __name__ == "__main__":
    main()