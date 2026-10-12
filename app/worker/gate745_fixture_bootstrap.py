"""Fixed synthetic fixture backend. Public CLI has no trusted hosted adapter.

No provider, Settings, ordinary startup, arbitrary SQL/DSN/dataset flags, or
production predeploy is connected to this module. UNBOUND precedes env/DB.
"""

import argparse
import asyncio
import hashlib
import json
import os
import re
import sys
from datetime import UTC, datetime
from importlib.metadata import Distribution
from pathlib import Path
from typing import Any, cast

from app.worker.gate745_fixture_policy import (
    BASELINE,
    GUARDED,
    PRODUCTION_IDS,
    Binding,
    ExecutionProof,
    FixtureDenied,
    Mode,
    TrustedFixtureExecutor,
    authorize,
    check_execution,
    check_runtime,
    digest,
    instant,
    load_binding,
)
from app.worker.gate745_fixture_recipe import (
    EMPTY_JOURNAL_DIGEST,
    JOURNAL_DIGEST,
    RECIPE_DIGEST,
    ROWS,
    canonical,
)


def target_digest(binding: Binding) -> str:
    value = {
        key: binding.target[key]
        for key in (
            "projectId",
            "environmentId",
            "serviceId",
            "postgresServiceId",
            "volumeInstanceId",
        )
    }
    value["repository"] = "AlphaCenter/gate"
    raw = json.dumps(value, separators=(",", ":"), ensure_ascii=True).encode()
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _known_tables() -> set[str]:
    # Same fixed schema imports as Gate's Alembic environment, without Settings
    # or any worker/provider initialization. Historical schemas are data-free.
    from app.models import (  # noqa: F401
        agent,
        alpha,
        alpha_preflight,
        alpha_transcription,
        alpha_voice,
        aqub,
        aqub_live,
        become,
        become_learner_invitation,
        become_program_draft,
        become_quest,
        become_recovery,
        beta_context,
        beta_material,
        beta_space,
        beta_wedding_voice,
        billing,
        canonical,
        relationship_crm,
        watch_voice,
    )
    from app.models.domain import Base

    return set(Base.metadata.tables) - {"reply_dialog_versions"}


MIGRATION_SHA256 = {
    "0001_initial_gate_domain.py": (
        "e3398aec771e073901daa2f216fe863525583106c29ec99036d4b3de1321bf9f"
    ),
    "0002_durable_message_storage.py": (
        "2289db60c59490c51e06e089fb6d7076bd5d1c62e32cc37eb768dae898c9d835"
    ),
    "0003_conversations_reply_tasks.py": (
        "81bba9a67ebc66488e45f940d3f7a3b0f6239bd635b218e744919e5ceae09aea"
    ),
    "0004_reply_drafts.py": (
        "f54210fa12f11f3d3a8271c744842b9e2031cc19492b1db80fd1c28acbbcac3d"
    ),
    "0005_multi_account_routing.py": (
        "c69d9d86100a19ec7c8c2a293858bf77fe8f29136aec3dcc05a52d61ed46bc33"
    ),
    "0006_business_message_direction.py": (
        "e566dbc59fd435d115f31e515b22a2b9ae244ab2a8533abfff3f08c7f5c6e17a"
    ),
    "0007_owner_identity_direction.py": (
        "ba96a497b90afd85e3b4591a801a50b11ba9386e8c9c7c2b71e5e31cbc12e27d"
    ),
    "0008_reply_send_attempts.py": (
        "deb1540bd9095968e8d354728205c237339efee47bffa8391750a2426047fd1d"
    ),
    "0009_account_routing_repair.py": (
        "e6ebc1629401a00e3ee01559e9e6c4b74ab93e38cb9b4db5b30992bfc36ea2b5"
    ),
    "0010_legacy_telegram_routing.py": (
        "b89b64738bb7a8c99e27dced0a703ee9aa798b90ca1186bb805ba492195a706b"
    ),
    "0011_confirmed_account_routes.py": (
        "396f69ca29ca28eeb2654d6a2c2d01e639954e6da2909cb6723c7b712ae42492"
    ),
    "0012_mark_workspace_owner.py": (
        "930562ac0afb8189bd382c39da8778c3f6700e84d7e5d639fdad16e58461b2d9"
    ),
    "0013_close_superseded_tasks.py": (
        "485316476fca14b73c9cf4bcfd6c9a4709d1cad7d6e6fce965896db976e66c60"
    ),
    "0014_telegram_receipt_outbox.py": (
        "a08ceeff73aff9f9c77458a334f5b01f4e89215c080b0ff0bb2e1b95ad33bdf1"
    ),
    "0015_telegram_queue_incidents.py": (
        "14fb8797bb47620613a0a587dc04bb5dce6a929e3ce1c8f749c462ace61a51ed"
    ),
    "0016_canonical_messaging_foundation.py": (
        "64a35a5ca35d1d082c1de92d8f22d4d2504456d41b628122b19847a83c512b8c"
    ),
    "0017_agent_kernel.py": (
        "1978d2465387aada069b35f3d9c0e7a02f9a7a03c2e5e62a833d3b2816e23f5a"
    ),
    "0018_alpha_delivery.py": (
        "9ae12892120faf61d224c5510f1d9150e73aa6a7810a4d1b5a717529a62aea92"
    ),
    "0019_alpha_lifecycle.py": (
        "b6ab00f4dca42167736c462ed6d9753ec6383498d940f8d65771c4a51674d3b2"
    ),
    "0020_alpha_canary_release.py": (
        "aaf8ee1c8b314c1b0ac6610727d4c6913611e181aac3cb5ce819f7fddeb1b745"
    ),
    "0021_enable_alpha_canary.py": (
        "83003133b40f6eba9de853e7c9187c0af655b82c58672d795f32dac32a1abeea"
    ),
    "0022_alpha_bundle_digest.py": (
        "de42efaf8abb8369a049358198defe97105108f21e6d2c693f00d6ec771e1426"
    ),
    "0023_alpha_model_release.py": (
        "b9ea11da4a544bd335de69d80f47566db53fe56a6c5ed240ece0aa5a6dd474e5"
    ),
    "0024_pause_alpha_canary_drill.py": (
        "0332111c98f549467447b7a7cd166af26aadc3aca70d57be81592f7d0e4d5c9e"
    ),
    "0025_resume_alpha_canary_drill.py": (
        "7125626079ea46b605648c49044db62d72264514411103aa490841dd90ddb7c7"
    ),
    "0026_alpha_model_preflight.py": (
        "18b8eaa56f21acf4d22b8c5a05eadda58c0c399ce7685ca37722cca3f2f086c9"
    ),
    "0027_alpha_voice_intake.py": (
        "250da2c32c9423b9895dbed5cfe887235f34b82818685eb2c005acb061e5398a"
    ),
    "0028_relationship_crm_core.py": (
        "17ce0535078cad168d06a1b621a7f38ab1027282d9cca9e1348723ca346cac89"
    ),
    "0029_relationship_crm_personal.py": (
        "e9576802986ded3866ab5c19ea841292f45cc62d3f1fbfe7e6a2407f28ce0104"
    ),
    "0030_relationship_crm_commercial.py": (
        "1f2a91676863d8182e5a75817130d8da4e52222a468f8c621d4d3cf5e6562891"
    ),
    "0031_billing_ledger.py": (
        "570cc2eef72f5afec47e400c85886138568bf803f29ca51b86a3b173a4b5619c"
    ),
    "0032_gate_voice_entitlement.py": (
        "2e9600c484918b9626fce887ef95150b11d368409a7d13b8461d5c122f9956c4"
    ),
    "0033_canonical_input_origin.py": (
        "35ae2ebd79833b20a9d7fd12d912b32644a7a120db33d275557ea749ae96ec07"
    ),
    "0034_alpha_model_v2_release.py": (
        "2c684656435fa3a424f929974f559d58381f90ff87f4b4683f9e3f5df4c066b1"
    ),
    "0035_alpha_rate_limit_deferral.py": (
        "a20dd7714d4e4eb4e85a2f20f8e13aaac9db2b858eb833b1e3f4297e211d8290"
    ),
    "0036_business_text_parity_evidence.py": (
        "eafbe41bb4d793d27002b9ed334ee303444a82d69c3b036b02c564188cc1c4d6"
    ),
    "0037_account_relative_direction_parity.py": (
        "ab7175b8c5306d319239eeaa2d1d6b3bfb3722f3acce710a73cbb6dd0fb3c480"
    ),
    "0038_business_text_canonical_cutover.py": (
        "923c70b9568a241be4b6d286157c827a0a19c5f1496e3a31a7d10e64e322b9f5"
    ),
    "0039_telegram_voice_archive.py": (
        "efd4a4bdd63208aeb115aad54e8b65bdc3ad9a66b6dce442e93743c13892b019"
    ),
    "0040_alpha_model_v2_bounded_live.py": (
        "64ff25085fee0c32c09ccb0685bd08231f6ffe4a4d65114572853f4075be3781"
    ),
    "0041_voice_transcript_store.py": (
        "18ed59659d156d4535b4d1c61106ef1a6899eeae62c48003995717088c46e98c"
    ),
    "0042_dropbox_phone_calls.py": (
        "09b7035101ff61d5aeeb1d460c8ed93daec9dc5d33f38b0aa7af43851fecc892"
    ),
    "0043_alpha_model_v2_installation_binding.py": (
        "02e335c2b7c3fa55d15c45cb54965d869c7ae5430cc0d156dce342b61e60c7f5"
    ),
    "0044_universal_mayak_owner_release.py": (
        "728e6208565b93de917cfaa967750e19fab0c9615d1be79b875b6aeff84c1747"
    ),
    "0045_mayak_owner_route.py": (
        "4432bc6570356c8eeba8c340e094c5337f68ca67b57c867cdedb1c5ff843cbc1"
    ),
    "0046_mayak_installation_reconcile.py": (
        "e916ac24cb47037d3d3508365232e2c8322f0a1a2cfdbee3c107527dba76ab35"
    ),
    "0047_mayak_exact_identity_reconcile.py": (
        "b05ea97a9e6e78de60e51107ca4f26620009737d8df74598cffced0a902bed1f"
    ),
    "0048_mayak_connector_binding.py": (
        "a3b957a2d4d0f1de0910286b2bf0ff16613441244eba2a93679e3983688bfaa9"
    ),
    "0049_mayak_connector_activation.py": (
        "59bcce6ec013c55c1d5b5d05099de15bd43f28863b1ece74869b87b32f03e576"
    ),
    "0050_beta_context_pilot.py": (
        "1707cab5e5a192f7b6dbb0af70dde835f1853c0317cf51adc910eaa767ed8696"
    ),
    "0051_beta_alexander_model.py": (
        "0de3454fab106f103df799e85fe147407545108c97fa78cafadaa6d77b729d9c"
    ),
    "0052_beta_alexander_exact_ref.py": (
        "85e734cdad3fd88641d4231db483b6651397b8e6b3621d7d56e5382dd3ddeb97"
    ),
    "0053_audio_closeout.py": (
        "f8c9e8f2f7ecd315993ebe6c5391937ceca90fe336b1c73e24cf2bb05bc4e554"
    ),
    "0054_audio_provider_limit.py": (
        "7e56856d7327d694498ed83b1488f4616b6c3502caf9cebada90f77ed7faa305"
    ),
    "0055_audio_provider_headroom.py": (
        "51de94605f898e7dc4a5d7b0d279a729703e350ce5c79997c2a15035782e6975"
    ),
    "0056_audio_provider_diagnostics.py": (
        "e596310d07773dd40d349746016378d2df0a20a6e1be9f78c15c99223a80c68f"
    ),
    "0057_dropbox_call_claim.py": (
        "71782045dffd08595c62304c0b6577ec9651ddda7b78c027d504053df3a507a1"
    ),
    "0058_beta_checkpoint_exports.py": (
        "42e930c456917b42abe1daf4aa7c8d11259ef69068d1f8416a77d74ba96cd1f8"
    ),
    "0058_long_call_chunking.py": (
        "4da7a8a1d0f485e0868e098b07ab83fdc50acb5048848222fcd00c3247d011a2"
    ),
    "0059_merge_audio_beta_heads.py": (
        "be714e1d8adee048501c16ed7e9cd12c17a5c7939f4897119a98a4ee30f12f9f"
    ),
    "0060_phone_call_processing_status.py": (
        "f3b2fbe61e5540687517980ea2184709b75a264d07825ea72727372c3111ac0c"
    ),
    "0061_voice_dead_letter_reconciliation.py": (
        "4ce43b4d59cb17e33e8173afb0721f8e32a111398df5de36e9bee3d0d50e1d4d"
    ),
    "0062_mayak_oko_v2.py": (
        "c71c4746908d84a1eb89ba4b67a9ca422b021bee1cd8f795320a50eb1f9eb2bf"
    ),
    "0062_voice_transport_unknown_retry.py": (
        "bbb36c99a84f044d0608e1d26f06215a0e028e01a03b02cfa534f421277dd49d"
    ),
    "0063_rejected_long_call_wav_retry.py": (
        "a4afab2a64c260f7125d8e4b3d7713edc6eb7ee4ced6e148ae0b0ee7b6e07ae6"
    ),
    "0064_long_call_claim_v8.py": (
        "cf16ec761978a5911e6d6daf72e7b04bb9bd3487a38e327c37abefdade6013b4"
    ),
    "0065_long_call_claim_v9.py": (
        "cd22b7fc549d1471ab038e2130284c959e5f6aed6c6b158acfc9a3dbafc78d3b"
    ),
    "0066_long_call_claim_v10.py": (
        "52e60a3ff6f350fd4a79186749846b09907a9c789876656942dd5f4f11079a9d"
    ),
    "0067_long_call_claim_v11.py": (
        "5c11bfd2de6908b5167ba7e5ed539ff29b67cd76e9b19ce57fbb662fe63335ba"
    ),
    "0068_long_call_claim_v12.py": (
        "7857ee717c33b321eac6824e1b134cb5f1683dd3d23157ebd35d99abb40ab453"
    ),
    "0069_long_call_claim_v13.py": (
        "3424ad33bce0fea48920c8c91cd54a6d871bb04c87fb1aa72330b3f7a2b6c0cc"
    ),
    "0071_become_controlled_intake.py": (
        "ced5b67830b9cf58906f8279000b29404dbed52814f3d3e974ac8894de2f4040"
    ),
    "0072_become_controlled_consent.py": (
        "42aba8bfe1fd2e1b4433c5f51085f9987eca425584a98a3f8f71fd73f5b2b28f"
    ),
    "0073_become_relations_handoff.py": (
        "86f36cb3c42b44cd1b144463dff56d6480535e4b7692ffbad7c8101c5282c29e"
    ),
    "0074_become_relations_delivery.py": (
        "da3dc197b30ad4efcacc028132b1897382f14a1a4e7996ff0622c9d298748cdf"
    ),
    "0075_become_relations_execution.py": (
        "566461356f3766e3e1b21ce33913257bf3b545b7989d6a2a331e354a7a93e83c"
    ),
    "0076_become_media_intake.py": (
        "a0eba8ca7b7b3406d19c8a2044dd913b2e9dd4c1e1a9e0f971d5aa42d27bc850"
    ),
    "0077_become_media_prompt.py": (
        "d835a882ecb53f100893acbdd938b373e7263404cb814323708d83f9a210dfde"
    ),
    "0078_become_voice_chain.py": (
        "e7270dbdf171901573dbefff06f38bebffcad3bb61b44a7fb4f2626f0f5b3f5b"
    ),
    "0079_become_voice_transcription.py": (
        "115da216ddffc9f318c5be85f6faa1b9e3de27d5e16e2fcd0844d6d052df3c4f"
    ),
    "0080_become_i4_recovery.py": (
        "c3bcdf507121e667657a81aeff478fb6403507a2a6c5a30f2748c8ac901c3cf2"
    ),
    "0081_become_i4_recovery_v2.py": (
        "11df4c6d7ee7df0720758900007aac3b49ef56a452a0a87c0c0ce51fcdae2ccc"
    ),
    "0082_become_quest.py": (
        "ab44ee89ffa566346130bbab2f8162daeb3364cae83054f6bda2cffda833f9e5"
    ),
    "0083_alpha_transcription_product.py": (
        "1600f7dec29ee512eaa7cf48915c0de787f5ac674a77574722b6c471f9f28467"
    ),
    "0083_become_quest_corrective.py": (
        "c6bdffcaf4980d293bb2a50e36d3ea2f1518ec7637c9c7db8325fd7ede92f3fc"
    ),
    "0083_become_quest_corrective_merge.py": (
        "c90f1fc1e46c0a0d279106fd9359e59504ae2268d36b420c464aaed0d33378fe"
    ),
    "0084_become_pilot_slice1_review.py": (
        "c3006d8a1ebd9dae1ce95f3634fb96ff29532ff567a89ab1167032d8433092fb"
    ),
    "0085_become_pilot_slice2_program.py": (
        "a89bad1873e3dfaaf43c4ba0397d02e2837e06e52fc42ede3ed583e77cbfaad4"
    ),
    "0086_beta_katya_space.py": (
        "e17624591f03f564ef44cb40bc13302b1a9bf0b398397acc4c518ce0a9f03827"
    ),
    "0087_aqub_live_delivery.py": (
        "7051640c3232763280704503663aa3010ba70ca925a5de1046291ddb6c866e41"
    ),
    "0087_become_pilot_slice3_learner.py": (
        "7d00c9c3773f0be97775f2af5fb11c153830ce6c046f50d6d7c5479031af1ed3"
    ),
    "0088_beta_wedding_replies.py": (
        "707adf99915891fbd00465fd5c230fac27559a5e53d3c2d2e740a5d9d5e0e0a6"
    ),
    "0089_beta_wedding_voice_phases.py": (
        "872ceb70f328ee22913143fbe94e2219262ceb63587080927a0929bb294ba56d"
    ),
    "0090_beta_wedding_checkpoints.py": (
        "09812f68200e716b88a1cf662f08bb1009d482629b0f81975e9f46e1339bf791"
    ),
    "0091_beta_universal_material.py": (
        "35c4db3cb90788de0d9584713b01030aefc6f526c4e64efeab681d057bcf7947"
    ),
    "0092_aqub_installation_binding.py": (
        "84cb9c8452ee5536cbf4bef9821d5291e9f65a26d5870c63af898fd2e9b773b7"
    ),
    "0093_beta_checkpoint_dispatch.py": (
        "f41663fb82ed517ccc0ced4644d45ec79ec705204c3e10817119a20cfe3a97c7"
    ),
    "0094_beta_durable_workers.py": (
        "53045e66d40b83148ddcb7a57d5e16753cc4fde8be22082f155cc19d5d49b495"
    ),
    "0095_beta_checkpoint_wake_generation.py": (
        "7944be5815b81f998e369858b5be48d5489afc392f7c877d6ad44f9e3566bdac"
    ),
    "0096_beta_binding_provenance.py": (
        "18c3f17ba11191daaa02c75c2b85c5950d0c5f8d83fbb26e92677cf6b659c789"
    ),
    "0096_beta_space_search.py": (
        "2f4ac1a6f2ad462ba09ebf04f3bb608a205e88b1e112dd0e43b0b96fcd8d1ffe"
    ),
    "0097_beta_exact_scope.py": (
        "56a46818e38386167fbf1a05c9be55249a4296945dcbc87b57a752a59f2fac5b"
    ),
    "0099_telegram_caption_backfill.py": (
        "369cfdc71f0de5fd753a6dbbfcfcc92faf8fdae1740c8efe1ead0960590343a7"
    ),
    "0100_aqub_food_media.py": (
        "d64ad9f339b1b7c2bd471931aeb9a8048dbd06fb05074f5b1b37d9207a550366"
    ),
    "0101_aqub_food_media_async.py": (
        "f1761692116e7e1f6ec92af0162a33f9fc679bfd9836f659b2e866bddb4a69e1"
    ),
    "0102_aqub_general_routes.py": (
        "a62ef1c350877411ae81c4d28812cc3de3a039fd1fc2c110516caf6a14ac988a"
    ),
    "0103_aqub_food_routes.py": (
        "bcd8fc8c57f48e724c6b1dbe12844cedf0ebc94a5f09a4b106e3f6d7c047db9e"
    ),
    "0104_dropbox_call_source_limit.py": (
        "102019d857efb69ab27edc745ca1eec5c8e4a2fc8c1aba550d78cec7a1ffc671"
    ),
    "0105_seekable_audio_recovery.py": (
        "7a4d0c65c3621492b2aae3188303ec589b999dfdabe5a8155239c6ad629092de"
    ),
    "0106_owner_voice_recovery.py": (
        "8ac5661ca8cfce6090db8ef4b248cb125211bd6d2d341b2aab5dca5e06449c12"
    ),
    "0107_no_speech_recovery.py": (
        "4e9fb793899f71a3ee8359345833ef1a673f3c611ab13d6481e5bec66f82a3d7"
    ),
    "0108_short_voice_headroom.py": (
        "de3d8087f6f9f058fdd9b105989580f6c57e4262a8c5eaf879b36afebd3b05a8"
    ),
    "0109_personal_delivery_fence.py": (
        "58be44fe998f9386084dec57d9dc290431d09bf640f9697201e123c4827568c2"
    ),
    "0110_gate_voice_three_dollar_cap.py": (
        "798cdd52bc7f447f85ee6841eb703e3cc5c95c642ef0d8f3812b003d2f75f55e"
    ),
    "0111_reply_documents.py": (
        "5fab7d7acf286b215816dbd3ab0823fb531988ad806b2a6ca4a0b760699c1f32"
    ),
    "0112_watch_voice_inbox.py": (
        "8471040ccb8deeb01506d9f198510d991726b2b51cb8283eb8ab557c0c10258f"
    ),
    "0113_watch_voice_drain_runs.py": (
        "9c5ce600cc5c6c647ad937ee6d71513ef445f559d111c0ccfa079a862bb0fe57"
    ),
    "0114_watch_voice_recovery_claims.py": (
        "8194d333c74d734976890cb4f9a7e5a468d539b37bf922dc44171a2e58d10df7"
    ),
    "0115_watch_voice_post_payment.py": (
        "dbfaad981446af3a1a685708630b72f5edd78258f526ede69855010a8938836c"
    ),
}


def _migration_versions() -> Path:
    """Resolve only reviewed source or the fixed installed-wheel artifact.

    Runtime SHA/tree admission authenticates the helper code; this inventory
    additionally forbids a missing, substituted or extended migration bundle.
    No cwd, environment variable or caller-supplied path influences resolution.
    """
    module = Path(__file__).resolve()
    source_root = module.parents[2]
    if (source_root / "pyproject.toml").is_file():
        versions = source_root / "alembic" / "versions"
    else:
        # A checkout's egg-info may precede the installed distribution on
        # sys.path even though this module was loaded from site-packages.
        # Search only the executing module's installation root, and never
        # select arbitrarily between multiple matching distribution records.
        candidates = list(Distribution.discover(name="gate", path=[str(source_root)]))
        if len(candidates) != 1:
            raise FixtureDenied("E_FIXTURE_MIGRATION_ARTIFACT")
        installed_module = Path(
            str(candidates[0].locate_file("app/worker/gate745_fixture_bootstrap.py"))
        ).resolve()
        if installed_module != module:
            raise FixtureDenied("E_FIXTURE_MIGRATION_ARTIFACT")
        versions = Path(sys.prefix) / "share" / "gate745-fixture" / "alembic" / "versions"
    if not versions.is_dir() or versions.is_symlink():
        raise FixtureDenied("E_FIXTURE_MIGRATION_ARTIFACT")
    files = {p.name: p for p in versions.glob("*.py")}
    if set(files) != set(MIGRATION_SHA256):
        raise FixtureDenied("E_FIXTURE_MIGRATION_ARTIFACT")
    for name, expected in MIGRATION_SHA256.items():
        path = files[name]
        if path.is_symlink() or hashlib.sha256(path.read_bytes()).hexdigest() != expected:
            raise FixtureDenied("E_FIXTURE_MIGRATION_ARTIFACT")
    return versions


def _migration_config() -> Any:
    from alembic.config import Config

    config = Config()
    config.set_main_option(
        "script_location", str(Path(__file__).with_name("gate745_fixture_alembic"))
    )
    config.set_main_option("version_locations", str(_migration_versions()))
    return config


def _migrate(connection: Any) -> None:
    from alembic import command

    config = _migration_config()
    config.attributes["connection"] = connection
    command.upgrade(config, BASELINE)


def _identity(connection: Any, binding: Binding, *, hosted: object = None) -> None:
    from sqlalchemy import text

    if any(
        binding.target[key] in PRODUCTION_IDS
        for key in (
            "projectId",
            "environmentId",
            "serviceId",
            "postgresServiceId",
            "volumeInstanceId",
        )
    ):
        raise FixtureDenied("E_FIXTURE_PRODUCTION")
    # Actual physical PG identity, not just a caller-provided DB name/hash.
    row = (
        connection.execute(
            text(
                "SELECT current_database() AS name, inet_server_addr()::text AS address, "
                "inet_server_port() AS port, system_identifier::text AS system_identifier "
                "FROM pg_control_system()"
            )
        )
        .mappings()
        .one()
    )
    physical = {
        key: binding.target[key]
        for key in (
            "projectId",
            "environmentId",
            "postgresServiceId",
            "volumeInstanceId",
        )
    }
    physical.update(
        {
            "databaseName": row["name"],
            "serverAddress": row["address"],
            "serverPort": row["port"],
            "systemIdentifier": row["system_identifier"],
        }
    )
    if (
        row["name"] != "gate745_synthetic"
        or digest(physical) != binding.seed["databaseIdentityDigest"]
    ):
        raise FixtureDenied("E_FIXTURE_PHYSICAL_DATABASE")
    allowed_control = False
    if hosted is not None:
        from app.worker.gate745_fixture_hosted import HostedOperation, assert_admitted
        from app.worker.gate745_fixture_ledger import validate_control_schema

        if not isinstance(hosted, HostedOperation) or hosted.binding != binding:
            raise FixtureDenied("E_FIXTURE_V2_AUTHENTICATED_BINDING")
        assert_admitted(hosted)
        validate_control_schema(connection, allow_missing=hosted.proof.total_invocations == 1)
        allowed_control = True
    if (
        connection.execute(
            text(
                "SELECT count(*) FROM pg_namespace WHERE nspname NOT IN "
                "('public','pg_catalog','information_schema') AND nspname !~ '^pg_' "
                "AND (:allowed_control = false OR nspname <> 'gate745_fixture_control')"
            ),
            {"allowed_control": allowed_control},
        ).scalar_one()
        != 0
    ):
        raise FixtureDenied("E_FIXTURE_FOREIGN_SCHEMA")


def _tables(connection: Any, revision: str) -> Any:
    from sqlalchemy import BigInteger, Column, Float, MetaData, String, Table, inspect, text
    from sqlalchemy.dialects.postgresql import DOUBLE_PRECISION

    expected = _known_tables() | {"alembic_version"}
    if revision == GUARDED:
        expected.add("reply_dialog_versions")
    inspector = inspect(connection)
    if (
        set(inspector.get_table_names(schema="public")) != expected
        or inspector.get_view_names(schema="public")
        or inspector.get_materialized_view_names(schema="public")
        or inspector.get_foreign_table_names(schema="public")
    ):
        raise FixtureDenied("E_FIXTURE_SCHEMA")
    if (
        connection.execute(
            text(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='public'"
            )
        ).scalar_one()
        != 0
    ):
        raise FixtureDenied("E_FIXTURE_SCHEMA")
    metadata = MetaData()
    metadata.reflect(bind=connection, schema="public")
    from app.models.domain import Base

    for table in metadata.sorted_tables:
        if table.name == "alembic_version":
            expected_table = Table(
                "alembic_version", MetaData(), Column("version_num", String(32), primary_key=True)
            )
        elif table.name == "reply_dialog_versions":
            expected_table = Table(
                "reply_dialog_versions",
                MetaData(),
                Column("business_connection_id", String(255), primary_key=True),
                Column("chat_id", String(255), primary_key=True),
                Column("revision", BigInteger(), nullable=False),
            )
        else:
            expected_table = Base.metadata.tables[table.name]
        if set(table.columns.keys()) != set(expected_table.columns.keys()) or set(
            table.primary_key.columns.keys()
        ) != set(expected_table.primary_key.columns.keys()):
            raise FixtureDenied("E_FIXTURE_SCHEMA")
        for column in table.columns:
            expected_column = expected_table.columns[column.name]
            expected_type = expected_column.type
            # PostgreSQL stores unqualified FLOAT as DOUBLE PRECISION.
            # Normalize that one canonical spelling, never REAL or other
            # type/precision differences, and keep nullability exact.
            if type(expected_type) is Float and expected_type.precision is None:
                expected_type = DOUBLE_PRECISION()
            if column.nullable != expected_column.nullable or str(
                column.type.compile(dialect=connection.dialect)
            ) != str(expected_type.compile(dialect=connection.dialect)):
                raise FixtureDenied("E_FIXTURE_SCHEMA")
        if table.name == "reply_dialog_versions":
            checks = inspector.get_check_constraints(table.name, schema="public")
            if (
                len(checks) != 1
                or checks[0]["name"] != "ck_reply_dialog_version_nonnegative"
                or re.sub(r"[()\s]", "", checks[0]["sqltext"]) != "revision>=0"
                or inspector.get_indexes(table.name, schema="public")
                or inspector.get_unique_constraints(table.name, schema="public")
            ):
                raise FixtureDenied("E_FIXTURE_SCHEMA")
    return metadata


def _row_values(table: Any) -> dict[str, object]:
    recipe = ROWS[table.name]
    values: dict[str, object] = {}
    for column in table.columns:
        if column.name in recipe:
            values[column.name] = recipe[column.name]
        elif column.nullable:
            values[column.name] = None
        else:
            raise FixtureDenied("E_FIXTURE_RECIPE_SCHEMA")
    if not set(recipe) <= set(values):
        raise FixtureDenied("E_FIXTURE_RECIPE_SCHEMA")
    return values


def _prove(connection: Any) -> tuple[str, str]:
    from sqlalchemy import func, select, text

    versions = list(
        connection.execute(text("SELECT version_num FROM public.alembic_version")).scalars()
    )
    if len(versions) != 1 or versions[0] not in (BASELINE, GUARDED):
        raise FixtureDenied("E_FIXTURE_REVISION")
    revision = versions[0]
    metadata = _tables(connection, revision)
    # Count the whole fixed schema first; foreign rows never become payloads.
    for table in metadata.sorted_tables:
        if table.name == "alembic_version":
            continue
        count = connection.execute(select(func.count()).select_from(table)).scalar_one()
        if count != (1 if table.name in ROWS else 0):
            raise FixtureDenied("E_FIXTURE_NONLITERAL_STATE")
    for name in ROWS:
        table = metadata.tables[f"public.{name}"]
        row = dict(connection.execute(select(table)).mappings().one())
        if canonical(row) != canonical(_row_values(table)):
            raise FixtureDenied("E_FIXTURE_NONLITERAL_STATE")
    # All journal columns must be literal, including nullable provider/error
    # fields. The wire digest uses only the fixed recipe's journal keys.
    journal = metadata.tables["public.send_attempts"]
    row = dict(connection.execute(select(journal)).mappings().one())
    journal_digest = digest(canonical([{key: row[key] for key in ROWS["send_attempts"]}]))
    if journal_digest != JOURNAL_DIGEST:
        raise FixtureDenied("E_FIXTURE_UNKNOWN_JOURNAL")
    return revision, journal_digest


def _seed_empty(connection: Any) -> None:
    from sqlalchemy import func, select

    metadata = _tables(connection, BASELINE)
    for table in metadata.sorted_tables:
        if (
            table.name != "alembic_version"
            and connection.execute(select(func.count()).select_from(table)).scalar_one() != 0
        ):
            raise FixtureDenied("E_FIXTURE_SEED_NOT_EMPTY")
    for name in ROWS:  # Literal order follows the fixed FK recipe.
        table = metadata.tables[f"public.{name}"]
        connection.execute(table.insert().values(**_row_values(table)))


def _bootstrap_empty(connection: Any) -> None:
    from sqlalchemy import inspect, text

    inspector = inspect(connection)
    if (
        inspector.get_table_names(schema="public")
        or inspector.get_view_names(schema="public")
        or inspector.get_materialized_view_names(schema="public")
        or inspector.get_foreign_table_names(schema="public")
        or connection.execute(
            text(
                "SELECT count(*) FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace "
                "WHERE n.nspname='public'"
            )
        ).scalar_one()
        != 0
        or connection.execute(
            text(
                "SELECT count(*) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace "
                "WHERE n.nspname='public'"
            )
        ).scalar_one()
        != 0
    ):
        raise FixtureDenied("E_FIXTURE_BOOTSTRAP_NOT_EMPTY")
    _migrate(connection)
    metadata = _tables(connection, BASELINE)
    # Fresh Gate migrations seed historical reference accounts/projects. Clear
    # only the schema just created from an authenticated EMPTY fixture DB,
    # never read/copy those rows and never use a production dump/restore.
    names = sorted(t.name for t in metadata.sorted_tables if t.name != "alembic_version")
    identifiers = ",".join(f'public."{name}"' for name in names)
    connection.execute(text("TRUNCATE TABLE " + identifiers + " CASCADE"))
    _seed_empty(connection)


def _operate(
    connection: Any, binding: Binding, proof: ExecutionProof, *, hosted: object = None
) -> dict[str, object]:
    from sqlalchemy import text

    _identity(connection, binding, hosted=hosted)
    if (
        binding.seed["recipeDigest"] != RECIPE_DIGEST
        or binding.seed["unknownJournalDigest"] != JOURNAL_DIGEST
    ):
        raise FixtureDenied("E_FIXTURE_RECIPE_BINDING")
    before_count, before_digest = 0, EMPTY_JOURNAL_DIGEST
    if proof.mode == "bootstrap":
        _bootstrap_empty(connection)
    elif proof.mode == "seed":
        _seed_empty(connection)
    else:
        _, before_digest = _prove(connection)
        before_count = 1
        if proof.mode == "reset":
            # Quiescence/retained artifact/fresh reset authority must already
            # be authenticated by the executor. Never accepts SQL selectors.
            connection.execute(text("DROP SCHEMA public CASCADE"))
            connection.execute(text("CREATE SCHEMA public"))
            _bootstrap_empty(connection)
    revision, after_digest = _prove(connection)
    return {
        "schemaRevision": revision,
        "reserved": 0,
        "sending": 0,
        "unknownCount": 1,
        "unknownJournalDigest": after_digest,
        "beforeUnknownCount": before_count,
        "beforeUnknownJournalDigest": before_digest,
    }


async def _execute_admitted(
    binding: Binding,
    executor: TrustedFixtureExecutor,
    proof: ExecutionProof,
) -> dict[str, object]:
    """Recheck installed binding before the credential/container boundary."""
    now = datetime.now(UTC)
    if binding != load_binding(now=now):
        raise FixtureDenied("E_FIXTURE_INSTALLED_BINDING")
    check_runtime(binding, os.environ.get)
    check_execution(binding, proof, proof.mode, now=now)
    from sqlalchemy.engine import make_url
    from sqlalchemy.ext.asyncio import create_async_engine
    from sqlalchemy.pool import NullPool

    url = make_url(executor.database_url())
    if (
        url.drivername != "postgresql+asyncpg"
        or url.host != binding.target["databaseHost"]
        or url.database != binding.target["databaseName"]
        or url.username != binding.target["databaseUser"]
        or url.port != 5432
        or url.query
        or not url.password
    ):
        raise FixtureDenied("E_FIXTURE_DSN_BINDING")
    engine = create_async_engine(
        url,
        poolclass=NullPool,
        connect_args={
            "timeout": 5,
            "server_settings": {
                "statement_timeout": "15000",
                "lock_timeout": "2000",
            },
        },
    )
    remaining = (instant(binding.authority["expiresAt"]) - datetime.now(UTC)).total_seconds()
    timeout = min(10 if proof.mode == "unknown-proof" else 120, remaining)
    if timeout <= 0:
        await engine.dispose()
        raise FixtureDenied("E_FIXTURE_EXPIRED")
    try:
        async with asyncio.timeout(timeout):
            async with engine.begin() as connection:
                if proof.mode == "unknown-proof":
                    from sqlalchemy import text

                    await connection.execute(text("SET TRANSACTION READ ONLY"))
                state = await connection.run_sync(lambda conn: _operate(conn, binding, proof))
    finally:
        await engine.dispose()
    return {
        "schemaVersion": "gate745.fixture-bootstrap-receipt/v1",
        "mode": proof.mode,
        "result": "SUCCEEDED",
        **{
            key: binding.source[key]
            for key in (
                "helperSourceSha",
                "helperSourceTree",
                "currentSourceSha",
                "currentSourceTree",
                "setupRouteDigest",
                "proofRouteDigest",
            )
        },
        "helperManifestDigest": binding.manifest_digest,
        "recipeDigest": RECIPE_DIGEST,
        "databaseIdentityDigest": binding.seed["databaseIdentityDigest"],
        "targetDigest": target_digest(binding),
        "ownerGrantCommentId": proof.owner_grant_comment_id,
        "ownerGrantBodyDigest": proof.owner_grant_body_digest,
        "runId": proof.run_id,
        "runAttempt": proof.run_attempt,
        "observedAt": datetime.now(UTC).isoformat(timespec="milliseconds").replace("+00:00", "Z"),
        **state,
        "seedDigest": RECIPE_DIGEST,
        "quiescenceReceiptDigest": proof.quiescence_receipt_digest,
        "retainedCaseReceiptDigest": proof.retained_case_receipt_digest,
        "resetGrantCommentId": proof.reset_grant_comment_id,
        "resetGrantBodyDigest": proof.reset_grant_body_digest,
        "applyPlan": False,
        "fixtureAcceptance": "NOT_PROVEN",
    }


async def run(mode: Mode) -> dict[str, object]:
    """Fixed CLI admission order. No trusted hosted route is installed."""
    now = datetime.now(UTC)
    binding = load_binding(now=now)
    check_runtime(binding, os.environ.get)
    # No command-line/env JSON/function can fabricate the missing executor.
    executor, proof = await authorize(mode, binding, None, now=now)
    return await _execute_admitted(binding, executor, proof)


async def fixture_proof(request: Any, authorization: str | None) -> dict[str, object]:
    """Fixed readonly endpoint admission, no Settings or shared admin secret."""
    from hmac import compare_digest

    now = datetime.now(UTC)
    binding = load_binding(now=now)
    check_runtime(binding, os.environ.get)
    executor, proof = await authorize(
        "unknown-proof",
        binding,
        getattr(request.app.state, "gate745_fixture_executor", None),
        now=now,
    )
    if binding != load_binding(now=datetime.now(UTC)):
        raise FixtureDenied("E_FIXTURE_INSTALLED_BINDING")
    check_runtime(binding, os.environ.get)
    # Dedicated server-side fixture binding may be read only after all control
    # admission. Current ordinary app never installs this executor.
    token = executor.fixture_admin_token()
    if (
        not token
        or not authorization
        or not authorization.startswith("Bearer ")
        or not compare_digest(authorization[7:], token)
    ):
        raise FixtureDenied("E_FIXTURE_ADMIN_AUTH")
    return await _execute_admitted(binding, executor, proof)


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Default-off fixed Gate745 synthetic fixture helper"
    )
    parser.add_argument("mode", choices=("bootstrap", "seed", "reset", "unknown-proof"))
    args = parser.parse_args()
    try:
        result = asyncio.run(run(cast(Mode, args.mode)))
    except FixtureDenied as exc:
        print(
            json.dumps(
                {
                    "result": "DENIED",
                    "code": exc.code,
                    "fixtureAcceptance": "NOT_PROVEN",
                    "applyPlan": False,
                }
            )
        )
        raise SystemExit(1) from None
    except Exception:
        print(
            json.dumps(
                {
                    "result": "DENIED",
                    "code": "E_FIXTURE_EXECUTION",
                    "fixtureAcceptance": "NOT_PROVEN",
                    "applyPlan": False,
                }
            )
        )
        raise SystemExit(1) from None
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
