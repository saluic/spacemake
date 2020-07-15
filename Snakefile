#########
# about #
#########
__version__ = '0.1.1'
__author__ = ['Nikos Karaiskos', 'Tamas Ryszard Sztanka-Toth']
__licence__ = 'GPL'
__email__ = ['nikolaos.karaiskos@mdc-berlin.de', 'tamasryszard.sztanka-toth@mdc-berlin.de']

###########
# imports #
###########
import os
import pandas as pd
import numpy as np
import math

################
# Shell prefix #
################
shell.prefix('set +o pipefail; JAVA_TOOL_OPTIONS="-Xmx8g -Xss2560k" ; umask g+w; ')

#############
# FUNCTIONS #
#############
include: 'snakemake_helper_functions.py'

####
# this file should contain all sample information, sample name etc.
####
# configfile should be loaded from command line

###############
# Global vars #
###############
repo_dir = os.path.dirname(workflow.snakefile)

# set root dir where the processed_data goes
project_dir = config['root_dir'] + '/projects/{project}'

microscopy_root = '/data/rajewsky/slideseq_microscopy'
microscopy_raw = microscopy_root + '/raw'

illumina_projects = config['illumina_projects']

# get the samples
project_df = pd.concat([read_sample_sheet(ip['sample_sheet'], ip['flowcell_id']) for ip in illumina_projects], ignore_index=True)

# add additional samples from config.yaml, which have already been demultiplexed. add none instead of NaN
project_df = project_df.append(config['additional_illumina_projects'], ignore_index=True).replace(np.nan, 'none', regex=True)

samples = create_lookup_table(project_df)
samples_list = project_df.T.to_dict().values()

# put all projects into projects_puck_info
projects_puck_info = project_df.merge(get_sample_info(microscopy_raw), how='left', on ='puck_id').fillna('none')

projects_puck_info.loc[~projects_puck_info.puck_id.str.startswith('PID_'), 'puck_id']= 'no_puck'

projects_puck_info['type'] = 'normal'

# added samples to merged to the projects_puck_info
# this will be saved as a metadata file in .config/ directory
if 'samples_to_merge' in config:
    for project_id in config['samples_to_merge'].keys():
        for sample_id in config['samples_to_merge'][project_id].keys():
            samples_to_merge = config['samples_to_merge'][project_id][sample_id]

            samples_to_merge = projects_puck_info.loc[projects_puck_info.sample_id.isin(samples_to_merge)]

            new_row = projects_puck_info[(projects_puck_info.project_id == project_id) & (projects_puck_info.sample_id == sample_id)].iloc[0]
            new_row.sample_id = 'merged_' + new_row.sample_id
            new_row.project_id = 'merged_' + new_row.project_id
            new_row.type = 'merged'
            new_row.experiment = ','.join(samples_to_merge.experiment.to_list())
            new_row.investigator = ','.join(samples_to_merge.investigator.to_list())
            new_row.sequencing_date = ','.join(samples_to_merge.sequencing_date.to_list())

            projects_puck_info = projects_puck_info.append(new_row, ignore_index=True)

demux_dir2project = {s['demux_dir']: s['project_id'] for s in samples_list}

# global wildcard constraints
wildcard_constraints:
    sample='(?!merged_).+',
    project='(?!merged_).+'

#################
# DIRECTORY STR #
#################
raw_data_root = project_dir + '/raw_data'
raw_data_illumina = raw_data_root + '/illumina'
raw_data_illumina_reads = raw_data_illumina + '/reads/raw'
raw_data_illumina_reads_reversed = raw_data_illumina + '/reads/reversed'

processed_data_root = project_dir + '/processed_data/{sample}'
processed_data_illumina = processed_data_root + '/illumina'

projects_puck_info_file = config['root_dir'] + '/.config/projects_puck_info.csv'

##############
# Demux vars #
##############
# Undetermined files pattern
# they are the output of bcl2fastq, and serve as an indicator to see if the demultiplexing has finished
demux_dir_pattern = config['root_dir'] + '/demultiplex_data/{demux_dir}'
demux_indicator = demux_dir_pattern + '/indicator.log'

####################################
# FASTQ file linking and reversing #
####################################
reads_suffix = '.fastq.gz'

raw_reads_prefix = raw_data_illumina_reads + '/{sample}_R'
raw_reads_pattern = raw_reads_prefix + '{mate}' + reads_suffix
raw_reads_mate_1 = raw_reads_prefix + '1' + reads_suffix
raw_reads_mate_2 = raw_reads_prefix + '2' + reads_suffix

reverse_reads_prefix = raw_data_illumina_reads_reversed + '/{sample}_reversed_R'
reverse_reads_pattern = reverse_reads_prefix + '{mate}' + reads_suffix
reverse_reads_mate_1 = reverse_reads_prefix + '1' + reads_suffix
reverse_reads_mate_2 = reverse_reads_prefix + '2' + reads_suffix

###############
# Fastqc vars #
###############
fastqc_root = raw_data_illumina + '/fastqc'
fastqc_pattern = fastqc_root + '/{sample}_reversed_R{mate}_fastqc.{ext}'
fastqc_command = '/data/rajewsky/shared_bins/FastQC-0.11.2/fastqc'
fastqc_ext = ['zip', 'html']

########################
# UNIQUE PIPELINE VARS #
########################
# set the tool script directories
picard_tools = '/data/rajewsky/shared_bins/picard-tools-2.21.6/picard.jar'
dropseq_tools = '/data/rajewsky/shared_bins/Drop-seq_tools-2.3.0'

# set per sample vars
dropseq_root = processed_data_illumina + '/complete_data'

data_root = dropseq_root
dropseq_reports_dir = dropseq_root + '/reports'
dropseq_tmp_dir = dropseq_root + '/tmp'
smart_adapter = config['adapters']['smart']

# file containing R1 and R2 merged
dropseq_merge_in_mate_1 = reverse_reads_mate_1
dropseq_merge_in_mate_2 = reverse_reads_mate_2
dropseq_merged_reads = dropseq_root + '/unaligned.bam'

#######################
# post dropseq and QC #
#######################
# umi cutoffs. used by qc-s and automated reports
umi_cutoffs = [1, 10, 50, 100]

#general qc sheet directory pattern
qc_sheet_dir = '/qc_sheet/umi_cutoff_{umi_cutoff}'

# parameters file for not merged samples
qc_sheet_parameters_file = data_root + qc_sheet_dir + '/qc_sheet_parameters.yaml'

# qc generation for ALL samples, merged and non-merged
united_illumina_root = config['root_dir'] + '/projects/{united_project}/processed_data/{united_sample}/illumina'
united_complete_data_root = united_illumina_root + '/complete_data'
united_qc_sheet = united_complete_data_root + qc_sheet_dir + '/qc_sheet_{united_sample}_{puck}.pdf'
united_star_log = united_complete_data_root + '/star_Log.final.out'
united_reads_type_out = united_complete_data_root + '/uniquely_mapped_reads_type.txt'
united_qc_sheet_parameters_file = united_complete_data_root + qc_sheet_dir + '/qc_sheet_parameters.yaml'
united_read_counts = united_complete_data_root + '/out_readcounts.txt.gz'
united_dge_all_summary = united_complete_data_root +  '/dge/dge_all_summary.txt'
united_dge_all_summary_fasta= united_complete_data_root +  '/dge/dge_all_summary.fa'
united_dge_all = united_complete_data_root +  '/dge/dge_all.txt.gz'
united_strand_info = united_complete_data_root + '/strand_info.txt'

# united final.bam
united_final_bam = united_complete_data_root + '/final.bam'

# automated analysis
automated_analysis_root = united_complete_data_root + '/automated_analysis/umi_cutoff_{umi_cutoff}'
automated_figures_root = automated_analysis_root + '/figures'
figure_suffix = '{united_sample}_{puck}.png'
automated_figures_suffixes = ['violin_filtered', 'pca_first_components',
    'umap_clusters','umap_top1_markers', 'umap_top2_markers']

automated_figures = [automated_figures_root + '/' + f + '_' + figure_suffix for f in automated_figures_suffixes]
automated_report = automated_analysis_root + '/{united_sample}_{puck}_illumina_automated_report.pdf'
automated_results_metadata = automated_analysis_root + '/{united_sample}_{puck}_illumina_automated_report_metadata.csv'

automated_results_file = automated_analysis_root + '/results.h5ad'

united_split_reads_root = united_complete_data_root + '/split_reads/'
united_unmapped_bam = united_split_reads_root + 'unmapped.bam'

united_split_reads_sam_names = ['plus_plus', 'plus_minus', 'minus_minus', 'minus_plus', 'plus_AMB', 'minus_AMB']
united_split_reads_sam_pattern = united_split_reads_root + '{file_name}.sam'
united_split_reads_bam_pattern = united_split_reads_root + '{file_name}.bam'

united_split_reads_sam_files = [united_split_reads_root + x for x in united_split_reads_sam_names]

united_split_reads_strand_type = united_split_reads_root + 'strand_type_num.txt'
united_split_reads_read_type = united_split_reads_root + 'read_type_num.txt'

# blast out
blast_header_out = "qseqid sseqid pident length mismatch gapopen qstart qend sstart send sstrand evalue bitscore"
united_barcode_blast_out = united_complete_data_root + '/cell_barcode_primer_blast_out.txt'

# downsample vars
downsample_root = united_illumina_root + '/downsampled_data'

# #######################
# include dropseq rules #
# #######################
include: 'dropseq.smk'

################################
# Final output file generation #
################################
def get_final_output_files(pattern, projects = 'all', **kwargs):
    if projects == 'all':
        samples = samples_list
    else:
        samples = [s for s in samples_list if s['project_id'] in projects]

    out_files = [expand(pattern,
            project=s['project_id'], 
            sample=s['sample_id'],
            puck=s['puck_id'], **kwargs) for s in samples]

    out_files = [item for sublist in out_files for item in sublist]
    
    return out_files

def get_united_output_files(pattern, projects = None, **kwargs):
    out_files = []
    df = projects_puck_info

    if projects is not None:
        df = df[df.project_id.isin(projects)]

    for index, row in df.iterrows():
        out_files = out_files + expand(pattern,
            united_project = row['project_id'],
            united_sample = row['sample_id'],
            puck=row['puck_id'], 
            **kwargs)

    return out_files

#############
# Main rule #
#############
rule all:
    input:
        get_final_output_files(dropseq_final_bam_ix),
        get_final_output_files(fastqc_pattern, ext = fastqc_ext, mate = [1,2]),
        get_united_output_files(united_qc_sheet, umi_cutoff = umi_cutoffs),
        get_united_output_files(united_strand_info),
        get_united_output_files(automated_report, umi_cutoff = umi_cutoffs),
        get_united_output_files(united_strand_info),
        # create blast results, blasting barcodes against primers
        get_united_output_files(united_barcode_blast_out),
        # get all split bam files
        get_united_output_files(united_unmapped_bam),
        get_united_output_files(united_split_reads_bam_pattern, file_name = united_split_reads_sam_names)


########################
# CREATE METADATA FILE #
########################
rule create_projects_puck_info_file:
    output:
        projects_puck_info_file
    run:
        projects_puck_info.to_csv(output[0], index=False)
        os.system('chmod 664 %s' % (output[0]))

################
# DOWNSAMPLING #
################
include: 'downsample.smk'

rule downsample:
    input:
        get_united_output_files(downsample_saturation_analysis, projects = config['downsample_projects'])
        #get_united_output_files(downsample_qc_sheet, projects = config['downsample_projects'], umi_cutoff = umi_cutoffs, ratio=downsampled_ratios)

#################
# MERGE SAMPLES #
#################
include: 'merge_samples.smk'

#########
# RULES #
#########
ruleorder: link_raw_reads > link_demultiplexed_reads 

rule demultiplex_data:
    params:
        demux_barcode_mismatch=lambda wildcards: samples[demux_dir2project[wildcards.demux_dir]]['demux_barcode_mismatch'],
        sample_sheet=lambda wildcards: samples[demux_dir2project[wildcards.demux_dir]]['sample_sheet'],
        flowcell_id=lambda wildcards: samples[demux_dir2project[wildcards.demux_dir]]['flowcell_id'],
        output_dir= lambda wildcards: expand(demux_dir_pattern, demux_dir=wildcards.demux_dir)
    input:
        unpack(get_basecalls_dir)
    output:
        demux_indicator
    threads: 16
    shell:
        """
        bcl2fastq \
            --no-lane-splitting --fastq-compression-level=9 \
            --mask-short-adapter-reads 15 \
            --barcode-mismatch {params.demux_barcode_mismatch} \
            --output-dir {params.output_dir} \
            --sample-sheet {params.sample_sheet} \
            --runfolder-dir  {input} \
            -r {threads} -p {threads} -w {threads}
            
            echo "demux finished: $(date)" > {output}
        """

rule link_demultiplexed_reads:
    input:
        ancient(unpack(get_demux_indicator))
    output:
        raw_reads_pattern
    params:
        demux_dir = lambda wildcards: expand(demux_dir_pattern, demux_dir=get_demux_dir(wildcards)),
        reads_folder = raw_data_illumina_reads
    shell:
        """
        mkdir -p {params.reads_folder}

        find {params.demux_dir} -type f -wholename '*/{wildcards.sample}/*R{wildcards.mate}*.fastq.gz' -exec ln -sr {{}} {output} \; 
        """

def get_reads(wildcards):
    return [samples[wildcards.project]['samples'][wildcards.sample]['R'+wildcards.mate]]

rule link_raw_reads:
    input:
        unpack(get_reads)
    output:
        raw_reads_pattern
    shell:
        """
        ln -s {input} {output}
        """

rule reverse_first_mate:
    input:
        raw_reads_mate_1
    output:
        reverse_reads_mate_1
    script:
        'reverse_fastq_file.py'

rule reverse_second_mate:
    input:
        raw_reads_mate_2
    output:
        reverse_reads_mate_2
    params:
        reads_folder = raw_data_illumina_reads_reversed
    shell:
        """
        mkdir -p {params.reads_folder}

        ln -sr {input} {output}
        """

rule run_fastqc:
    input:
        reverse_reads_pattern
    output:
        fastqc_pattern
    params:
        output_dir = fastqc_root 
    threads: 8
    shell:
        """
        mkdir -p {params.output_dir}

        {fastqc_command} -t {threads} -o {params.output_dir} {input}
        """

rule get_united_reads_type_out:
    input:
        united_split_reads_read_type
    output:
        united_reads_type_out
    shell:
        ## Script taken from sequencing_analysis.sh
        """
        ln -sr {input} {output}
        """

rule index_bam_file:
    input:
        dropseq_final_bam
    output:
        dropseq_final_bam_ix 
    shell:
       "samtools index {input}"

rule create_qc_parameters:
    params:
        sample_params=lambda wildcards: get_qc_sheet_parameters(wildcards.sample, wildcards.umi_cutoff)
    output:
        qc_sheet_parameters_file
    script:
        "qc_sequencing_create_parameters_from_sample_sheet.py"

rule create_qc_sheet:
    input:
        star_log = united_star_log,
        reads_type_out=united_reads_type_out,
        parameters_file=united_qc_sheet_parameters_file,
        read_counts = united_read_counts,
        dge_all_summary = united_dge_all_summary
    output:
        united_qc_sheet
    script:
        "qc_sequencing_create_sheet.py"

rule run_automated_analysis:
    input:
        united_dge_all
    output:
        res_file=automated_results_file
    params:
        fig_root=automated_figures_root
    script:
        'automated_analysis.py'
        
rule create_automated_report:
    input:
        star_log=united_star_log,
        res_file=automated_results_file,
        parameters_file=united_qc_sheet_parameters_file
    output:
        figures=automated_figures,
        report=automated_report,
        results_metadata=automated_results_metadata
    params:
        fig_root=automated_figures_root
    script:
        'automated_analysis_create_report.py'

rule create_strand_info:
    input:
        united_split_reads_strand_type
    output:
        united_strand_info
    shell:
        "ln -sr {input} {output}"

rule split_final_bam:
    input:
        united_final_bam
    output:
        temp(united_split_reads_sam_files),
        united_split_reads_read_type,
        united_split_reads_strand_type
    params:
        prefix=united_split_reads_root
    shell:
        "sambamba view -F 'mapping_quality==255' -h {input} | python {repo_dir}/scripts/split_reads_by_strand_info.py --prefix {params.prefix} /dev/stdin"

rule split_reads_sam_to_bam:
    input:
        united_split_reads_sam_pattern
    output:
        united_split_reads_bam_pattern
    threads: 2
    shell:
        "sambamba view -S -h -f bam -t {threads} -o {output} {input}"

rule get_unmapped_reads:
    input:
        united_final_bam
    output:
        united_unmapped_bam
    threads: 2
    shell:
        "sambamba view -F 'unmapped' -h -f bam -t {threads} -o {output} {input}"

rule create_dge_barcode_fasta:
    input:
        united_dge_all_summary
    output:
        united_dge_all_summary_fasta
    shell:
        """tail -n +8 {input} | awk 'NF==4 && !/^TAG=XC*/{{print ">{wildcards.united_sample}_"$1"_"$2"_"$3"_"$4"\\n"$1}}' > {output}"""

rule blast_dge_barcodes:
    input:
        db=repo_dir + '/sequences/primers.fa',
        barcodes= united_dge_all_summary_fasta
    output:
        united_barcode_blast_out
    threads: 2
    shell:
        """
        blastn -query {input.barcodes} -evalue 10 -task blastn-short -num_threads {threads} -outfmt "6 {blast_header_out}" -db {input.db} -out {output}"""
