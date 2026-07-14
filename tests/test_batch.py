"""
tests/test_batch.py

Unit tests for srw.batch config loading. Job launching itself (_run_job,
batch_main) shells out to `python -m srw run` and is not covered here —
validate it end-to-end against a real or synthetic set of batch directories.
"""

import pytest

from srw.batch import load_batch_config, BatchJob


def write_config(tmp_path, text):
    config_path = tmp_path / "batch.toml"
    config_path.write_text(text)
    return str(config_path)


class TestLoadBatchConfig:
    def test_derives_source_docs_mediaconch_from_batch_dir(self, tmp_path):
        config_path = write_config(tmp_path, """
[[batch]]
batch_dir = "/mnt/raid1/batch1"
output_dir = "/mnt/nvme_a/mkv"
""")
        jobs = load_batch_config(config_path)
        assert jobs == [
            BatchJob(
                name="raid1_batch1",
                source_dir="/mnt/raid1/batch1/Source",
                docs_dir="/mnt/raid1/batch1/Documents",
                mediaconch_dir="/mnt/raid1/batch1/MediaConch",
                output_dir="/mnt/nvme_a/mkv",
                attachment_size=None,
            )
        ]

    def test_explicit_name_overrides_derived_name(self, tmp_path):
        config_path = write_config(tmp_path, """
[[batch]]
name = "raid1_batch1"
batch_dir = "/mnt/raid1/batch1"
output_dir = "/mnt/nvme_a/mkv"
""")
        jobs = load_batch_config(config_path)
        assert jobs[0].name == "raid1_batch1"

    def test_individual_dir_overrides_win_over_batch_dir(self, tmp_path):
        config_path = write_config(tmp_path, """
[[batch]]
batch_dir = "/mnt/raid1/batch1"
source_dir = "/mnt/raid1/batch1/CustomSource"
output_dir = "/mnt/nvme_a/mkv"
""")
        jobs = load_batch_config(config_path)
        assert jobs[0].source_dir == "/mnt/raid1/batch1/CustomSource"
        assert jobs[0].docs_dir == "/mnt/raid1/batch1/Documents"

    def test_multiple_entries_matching_real_layout(self, tmp_path):
        # Mirrors the lab's actual deployment: three batch folders per RAID volume,
        # with the same batch1/batch2/batch3 naming repeated on each RAID, all
        # writing to that RAID's designated NVMe output drive.
        config_path = write_config(tmp_path, """
[[batch]]
batch_dir = "/mnt/raid1/batch1"
output_dir = "/mnt/nvme_a/mkv"

[[batch]]
batch_dir = "/mnt/raid1/batch2"
output_dir = "/mnt/nvme_a/mkv"

[[batch]]
batch_dir = "/mnt/raid2/batch1"
output_dir = "/mnt/nvme_b/mkv"
""")
        jobs = load_batch_config(config_path)
        assert len(jobs) == 3
        # Basename alone ("batch1") would collide between the raid1 and raid2
        # entries; the parent-qualified default name keeps them distinct.
        assert [j.name for j in jobs] == ["raid1_batch1", "raid1_batch2", "raid2_batch1"]

    def test_duplicate_names_are_rejected(self, tmp_path):
        config_path = write_config(tmp_path, """
[[batch]]
name = "batch1"
batch_dir = "/mnt/raid1/batch1"
output_dir = "/mnt/nvme_a/mkv"

[[batch]]
name = "batch1"
batch_dir = "/mnt/raid2/batch1"
output_dir = "/mnt/nvme_b/mkv"
""")
        with pytest.raises(ValueError, match="duplicate batch names"):
            load_batch_config(config_path)

    def test_missing_output_dir_is_rejected(self, tmp_path):
        config_path = write_config(tmp_path, """
[[batch]]
batch_dir = "/mnt/raid1/batch1"
""")
        with pytest.raises(ValueError, match="output_dir"):
            load_batch_config(config_path)

    def test_missing_batch_dir_and_source_dir_is_rejected(self, tmp_path):
        config_path = write_config(tmp_path, """
[[batch]]
output_dir = "/mnt/nvme_a/mkv"
""")
        with pytest.raises(ValueError, match="batch_dir"):
            load_batch_config(config_path)

    def test_empty_config_is_rejected(self, tmp_path):
        config_path = write_config(tmp_path, "")
        with pytest.raises(ValueError, match="No \\[\\[batch\\]\\]"):
            load_batch_config(config_path)

    def test_attachment_size_override(self, tmp_path):
        config_path = write_config(tmp_path, """
[[batch]]
batch_dir = "/mnt/raid1/batch1"
output_dir = "/mnt/nvme_a/mkv"
attachment_size = 8000000
""")
        jobs = load_batch_config(config_path)
        assert jobs[0].attachment_size == 8000000
