from revolverelate.errors import PolicyError
from revolverelate.policy.accept import accept_policy, default_policy
import pytest


def test_default_policy_tags_critical(schema):
    policy = accept_policy(default_policy(schema), schema)
    assert policy["attributes"]["Customer.Password"] == "critical"
    assert policy["attributes"]["Customer.Email"] == "pii"
    assert "mutate_live" not in policy["capabilities"]


def test_cannot_downgrade_critical(schema):
    proposed = default_policy(schema)
    proposed["attributes"]["Customer.Password"] = "public"
    with pytest.raises(PolicyError, match="downgrade"):
        accept_policy(proposed, schema)
