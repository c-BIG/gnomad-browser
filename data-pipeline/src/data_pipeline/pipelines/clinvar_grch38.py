from data_pipeline.pipeline import Pipeline, run_pipeline

from data_pipeline.data_types.variant import annotate_transcript_consequences

from data_pipeline.datasets.clinvar import prepare_clinvar_variants

from data_pipeline.pipelines.genes import pipeline as genes_pipeline


pipeline = Pipeline()

pipeline.add_download_task(
    "download_clinvar_grch38_vcf",
    "ftp://ftp.ncbi.nlm.nih.gov/pub/clinvar/vcf_GRCh38/clinvar.vcf.gz",
    "/external_sources/clinvar_grch38.vcf.gz",
)

pipeline.add_task(
    "prepare_clinvar_grch38_variants",
    prepare_clinvar_variants,
    "/clinvar/clinvar_grch38_base.ht",
    {"vcf_path": pipeline.get_task("download_clinvar_grch38_vcf")},
    {"reference_genome": "GRCh38"},
)

pipeline.add_task(
    "annotate_clinvar_grch38_transcript_consequences",
    annotate_transcript_consequences,
    "/clinvar/clinvar_grch38_annotated.ht",
    {
        "variants_path": pipeline.get_task("prepare_clinvar_grch38_variants"),
        "transcripts_path": genes_pipeline.get_task("extract_grch38_transcripts"),
        "mane_transcripts_path": genes_pipeline.get_task("import_mane_select_transcripts"),
    },
)

###############################################
# Run
###############################################

if __name__ == "__main__":
    run_pipeline(pipeline)
