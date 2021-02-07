import dataclasses
import pprint

import ccc.oci
import oci

__cmd_name__ = 'oci'


def cp(src:str, tgt:str):
    oci_client = ccc.oci.oci_client()

    oci.replicate_artifact(
        src_image_reference=src,
        tgt_image_reference=tgt,
        oci_client=oci_client,
    )


def ls(image: str):
    oci_client = ccc.oci.oci_client()

    print(oci_client.tags(image_reference=image))


def manifest(image_reference: str):
    oci_client = ccc.oci.oci_client()

    manifest = oci_client.manifest(image_reference=image_reference)

    pprint.pprint(dataclasses.asdict(manifest))
