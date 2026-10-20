"""OQ-44 (d): my IAM identity, as SQS and the draft tool see it.

SQS stamps each message with its sender's `SenderId`; for a role session it
is "<role ID>:<session name>". My `aws login` session is an AWS-managed role,
whose unique role ID is looked up once per cold start, so no ID is written
down anywhere.
"""

from functools import cache


@cache
def owner_role_id(role_name: str) -> str:
    import boto3

    return boto3.client("iam").get_role(RoleName=role_name)["Role"]["RoleId"]


def sender_role_id(sender_id: object) -> str:
    """The role ID part of an SQS SenderId, or "" for anything else."""
    if not isinstance(sender_id, str):
        return ""
    role_id, colon, _ = sender_id.partition(":")
    return role_id if colon and role_id.startswith("AROA") else ""
