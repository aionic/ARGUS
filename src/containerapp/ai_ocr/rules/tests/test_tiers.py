from ai_ocr.tiers import EffectiveConfig, resolve_effective_config


def test_tier_resolution_uses_env_default_and_deployments():
    config = resolve_effective_config(
        None,
        {},
        {
            "DEFAULT_EXTRACTION_TIER": "premium",
            "AZURE_OPENAI_MODEL_DEPLOYMENT_NAME": "gpt-4.1-prod",
            "SUMMARY_MODEL_DEPLOYMENT_NAME": "gpt-4.1-mini-summary",
            "ENABLE_IMAGE_PREPROCESSING": "true",
            "EXTRACTION_BACKEND": "gpt",
        },
    )

    assert isinstance(config, EffectiveConfig)
    assert config.tier == "premium"
    assert config.extraction_model == "gpt-4.1-prod"
    assert config.summary_model == "gpt-4.1-mini-summary"
    assert config.enable_evaluation
    assert config.enable_summary
    assert config.enable_preprocessing


def test_processing_options_override_preset_and_env_values():
    config = resolve_effective_config(
        None,
        {
            "tier": "economy",
            "enable_images": True,
            "enable_evaluation": True,
            "extraction_model": "custom-mini",
            "summary_model": "custom-summary",
        },
        {"DEFAULT_EXTRACTION_TIER": "premium", "ENABLE_IMAGE_PREPROCESSING": "false"},
    )

    assert config.to_dict() == {
        "tier": "economy",
        "extraction_model": "custom-mini",
        "enable_ocr": True,
        "enable_images": True,
        "enable_evaluation": True,
        "enable_summary": False,
        "summary_model": "custom-summary",
        "use_rules_engine": True,
        "enable_preprocessing": False,
    }


def test_explicit_tier_argument_overrides_processing_options_tier():
    config = resolve_effective_config(
        "premium",
        {"tier": "economy", "include_images": False, "include_ocr": False},
        {
            "AZURE_OPENAI_MODEL_DEPLOYMENT_NAME": "vision-deployment",
            "SUMMARY_MODEL_DEPLOYMENT_NAME": "summary-deployment",
        },
    )

    assert config.tier == "premium"
    assert config.extraction_model == "vision-deployment"
    assert config.summary_model == "summary-deployment"
    assert not config.enable_images
    assert not config.enable_ocr
