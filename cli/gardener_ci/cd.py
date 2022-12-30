import concurrent.futures
import logging
import sys

import ccc.oci
import cnudie.iter
import cnudie.retrieve
import cnudie.validate
import ctx
import gci.componentmodel as cm
import version

logger = logging.getLogger(__name__)


def retrieve(
    name: str,
    version: str,
    ctx_base_url: str=None,
    out: str=None
):
    if not ctx_base_url:
        ctx_base_url = ctx.cfg.ctx.ocm_repo_base_url

    ctx_repo = cm.OciRepositoryContext(
            baseUrl=ctx_base_url,
            componentNameMapping=cm.OciComponentNameMapping.URL_PATH,
        )

    component_descriptor = cnudie.retrieve.oci_component_descriptor_lookup()(
        component_id=cm.ComponentIdentity(
            name=name,
            version=version,
        ),
        ctx_repo=ctx_repo,
    )

    if out:
        outfh = open(out, 'w')
    else:
        outfh = sys.stdout

    component_descriptor.to_fobj(fileobj=outfh)
    outfh.flush()
    outfh.close()


def validate(
    name: str,
    version: str,
    ctx_base_url: str=None,
    out: str=None
):
    if not ctx_base_url:
        ctx_base_url = ctx.cfg.ctx.ocm_repo_base_url

    ctx_repo = cm.OciRepositoryContext(
            baseUrl=ctx_base_url,
            componentNameMapping=cm.OciComponentNameMapping.URL_PATH,
        )

    logger.info('retrieving component-descriptor..')
    component_descriptor = cnudie.retrieve.oci_component_descriptor_lookup()(
        component_id=cm.ComponentIdentity(
            name=name,
            version=version,
        ),
        ctx_repo=ctx_repo,
    )
    component = component_descriptor.component
    logger.info('validating component-descriptor..')

    violations = tuple(
        cnudie.validate.iter_violations(
            nodes=cnudie.iter.iter(
                component=component,
                recursion_depth=0,
            ),
        )
    )

    if not violations:
        logger.info('component-descriptor looks good')
        return

    logger.warning('component-descriptor yielded validation-errors (see below)')
    print()

    for violation in violations:
        print(violation.as_error_message)


def ls(
    name: str,
    greatest: bool=False,
    final: bool=False,
    ocm_repo_base_url: str=None,
):
    if not ocm_repo_base_url:
        ocm_repo_base_url = ctx.cfg.ctx.ocm_repo_base_url

    ctx_repo = cm.OciRepositoryContext(baseUrl=ocm_repo_base_url)

    if greatest:
        print(cnudie.retrieve.greatest_component_version(
            component_name=name,
            ctx_repo=ctx_repo,
        ))
        return

    versions = cnudie.retrieve.component_versions(
        component_name=name,
        ctx_repo=ctx_repo,
    )

    for v in versions:
        if final:
            parsed_version = version.parse_to_semver(v)
            if parsed_version.prerelease:
                continue
        print(v)


def purge_old(
    name: str,
    final: bool=False,
    ocm_repo_base_url: str=None,
    keep: int=256,
    threads: int=32,
):
    if not ocm_repo_base_url:
        ocm_repo_base_url = ctx.cfg.ctx.ocm_repo_base_url

    ctx_repo = cm.OciRepositoryContext(baseUrl=ocm_repo_base_url)

    versions = cnudie.retrieve.component_versions(
        component_name=name,
        ctx_repo=ctx_repo,
    )

    if not final:
        versions = [
            v for v in versions
            if not version.parse_to_semver(v).prerelease
        ]

    versions = version.smallest_versions(
        versions=versions,
        keep=keep,
    )

    print(f'will rm {len(versions)} version(s) using {threads=}')

    pool = concurrent.futures.ThreadPoolExecutor(max_workers=threads)
    oci_client = ccc.oci.oci_client(
        http_connection_pool_size=threads,
    )

    def purge_component_descriptor(ref: str):
        oci_client.delete_manifest(
            image_reference=ref,
            purge=True,
        )
        print(f'purged: {ref}')

    def iter_oci_refs_to_rm():
        for v in versions:
            ref = f'{ocm_repo_base_url}/component-descriptors/{name}:{v}'
            yield pool.submit(
                purge_component_descriptor,
                ref=ref,
            )

    for ref in concurrent.futures.as_completed(iter_oci_refs_to_rm()):
        pass
