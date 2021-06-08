PROJECT_DF_COLUMNS = ["sample_id", "puck_id", "project_id", "sample_sheet", "flowcell_id",
    "species", "demux_barcode_mismatch", "demux_dir", "R1", "R2", "investigator",
    "sequencing_date", "experiment", "puck_barcode_file", "downstream_analysis_type", "is_merged"]

# barcode flavor parsing and query functions
class dotdict(dict):
    """dot.notation access to dictionary attributes"""

    __getattr__ = dict.get
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__


def parse_barcode_flavors(
    config,
    bc_default_settings=dict(
        bc1_ref="",
        bc2_ref="",
        cell_raw="None",
        score_threshold=0.0,
        bam_tags="CR:{cell},MI:{UMI}",
    ),
):
    """
    Reads the 'barcode_flavor' top-level block of config.yaml as well
    as the corresponding block from 'knowledge'.
    Gathers all mappings of project_id -> bc_flavor and sample_id -> bc_flavor
    """
    default_barcode_flavor = "dropseq"
    project_barcode_flavor = {}
    sample_barcode_flavor = {}
    preprocess_settings = {}
    for flavor, v in config["barcode_flavor"].items():
        # for each flavor, also retrieve the configuration
        # first make a copy of the default values
        d = dict(bc_default_settings)
        d.update(config["knowledge"]["barcode_flavor"][flavor])
        preprocess_settings[flavor] = dotdict(d)

        if v == "default":
            default_barcode_flavor = flavor
            continue

        for name in v.get("projects", []):
            project_barcode_flavor[name] = flavor

        for name in v.get("samples", []):
            sample_barcode_flavor[name] = flavor

    res = dotdict(
        dict(
            default=default_barcode_flavor,
            projects=project_barcode_flavor,
            samples=sample_barcode_flavor,
            preprocess_settings=preprocess_settings,
        )
    )

    return res


def get_barcode_flavor(project_id, sample_id):
    default = bc_flavor_data.default
    project_default = bc_flavor_data.projects.get(project_id, default)
    return bc_flavor_data.samples.get(sample_id, project_default)


def df_assign_bc_flavor(df):
    # assign the barcode layout for each sample as specified in the config.yaml
    def flavor_choice(row):
        return get_barcode_flavor(row.project_id, row.sample_id)

    df["barcode_flavor"] = df[["project_id", "sample_id"]].apply(flavor_choice, axis=1)
    return df


def get_bc_preprocess_settings(wildcards):
    """
    This function will return a dictionary of information
    on the read1 preprocessing, according to barcode_flavor
    """
    flavor = get_barcode_flavor(wildcards.project, wildcards.sample)
    settings = bc_flavor_data.preprocess_settings[flavor]

    return settings


def get_bc_preprocessing_threads(wildcards):
    # 2 extra cores are needed for the zcat_pipes
    if hasattr(workflow, "cores"):
        # from at least Snakemake version 5.13 on
        t = workflow.cores - 2
    else:
        t = 8  # a safe default value?
        import logging

        logging.warning(
            "can not determine number of cores in this "
            f"Snakemake version. Defaulting to {t} for "
            "barcode preprocessing"
        )
    return t


# all barcode flavor info from config.yaml
# is kept here for convenient lookup
bc_flavor_data = parse_barcode_flavors(config)

def hamming_distance(string1, string2):
    return sum(c1 != c2 for c1, c2 in zip(string1, string2))


def compute_max_barcode_mismatch(indices):
    """computes the maximum number of mismatches allowed for demultiplexing based
    on the indices present in the sample sheet."""
    num_samples = len(indices)

    if num_samples == 1:
        return 4
    else:
        max_mismatch = 3
        for i in range(num_samples - 1):
            for j in range(i + 1, num_samples):
                hd = hamming_distance(indices[i], indices[j])
                max_mismatch = min(max_mismatch, math.ceil(hd / 2) - 1)
    return max_mismatch

def get_barcode_file(path):
    if path is None:
        return 'none'

    if os.path.isfile(path):
        return path

    return 'none'


def find_barcode_file(puck_id):
    # first find directory of puck file

    def find_dir(name, path):
        for root, dirs, files in os.walk(path):
            if name in dirs:
                return os.path.join(root, name)

    puck_dir = find_dir(puck_id, config['puck_data']['root'])
    path = None

    if puck_dir is not None:
        # puck dir exists, look for barcode file pattern
        path = os.path.join(puck_dir, config['puck_data']['barcode_file'])

    return get_barcode_file(path)
    
def read_sample_sheet(sample_sheet_path, flowcell_id):
    with open(sample_sheet_path) as sample_sheet:
        ix = 0
        investigator = "none"
        sequencing_date = "none"

        for line in sample_sheet:
            line = line.strip("\n")
            if "Investigator" in line:
                investigator = line.split(",")[1]
            if "Date" in line:
                sequencing_date = line.split(",")[1]
            if "[Data]" in line:
                break
            else:
                ix = ix + 1

    df = pd.read_csv(sample_sheet_path, skiprows=ix + 1)
    df["species"] = df["Description"].str.split("_").str[-1]
    df["investigator"] = investigator
    df["sequencing_date"] = sequencing_date

    # mock R1 and R2
    df["R1"] = "none"
    df["R2"] = "none"

    df["downstream_analysis_type"] = 'default'
    df["is_merged"] = False

    # merge additional info and sanitize column names
    df.rename(
        columns={
            "Sample_ID": "sample_id",
            "Sample_Name": "puck_id",
            "Sample_Project": "project_id",
            "Description": "experiment",
        },
        inplace=True,
    )
    df["flowcell_id"] = flowcell_id
    df["demux_barcode_mismatch"] = compute_max_barcode_mismatch(df["index"])
    df["sample_sheet"] = sample_sheet_path
    df["demux_dir"] = df["sample_sheet"].str.split("/").str[-1].str.split(".").str[0]
    df["puck_barcode_file"] = df.puck_id.apply(find_barcode_file)

    return df[PROJECT_DF_COLUMNS]    


def df_assign_merge_samples(project_df):
    # added samples to merged to the project_df
    # this will be saved as a metadata file in .config/ directory
    if 'samples_to_merge' in config:
        for project_id in config['samples_to_merge'].keys():
            for sample_id in config['samples_to_merge'][project_id].keys():
                samples_to_merge = config['samples_to_merge'][project_id][sample_id]

                samples_to_merge = project_df.loc[project_df.sample_id.isin(samples_to_merge)]

                new_row = project_df[(project_df.project_id == project_id) & (project_df.sample_id == sample_id)].iloc[0]
                new_row.sample_id = 'merged_' + new_row.sample_id
                new_row.project_id = 'merged_' + new_row.project_id
                new_row.is_merged = True
                new_row.experiment = ','.join(samples_to_merge.experiment.to_list())
                new_row.investigator = ','.join(samples_to_merge.investigator.to_list())
                new_row.sequencing_date = ','.join(samples_to_merge.sequencing_date.to_list())

                project_df = project_df.append(new_row, ignore_index=True)

    return project_df

def create_project_df():
    project_df = pd.DataFrame(columns=PROJECT_DF_COLUMNS)

    if projects is not None:
        # if we have projects in the config file
        # get the samples
        project_df = project_df.append(pd.concat(
            [read_sample_sheet(ip['sample_sheet'], ip['flowcell_id']) for ip in projects],
            ignore_index=True), ignore_index=True)

    # add additional samples from config.yaml, which have already been demultiplexed.
    for project in config['additional_projects']:
        project_series = pd.Series(project)
        project_series["is_merged"] = False
        
        project_index = project_df.loc[(project_df.project_id == project_series.project_id) & \
                (project_df.sample_id == project_series.sample_id)].index
        if not project_index.empty:
            # if index not empty, that is there is a sample in the dataframe with this id, update
            project_df.loc[project_index, project_series.keys()] = project_series.values
        else:
            # add project
            project_df = project_df.append(project_series, ignore_index=True)

    # remove empty fields and add 'none' instead
    project_df = project_df.replace(np.nan, 'none')

    project_df = df_assign_merge_samples(project_df)

    # fill downstream variables with default
    project_df.loc[project_df[project_df.downstream_analysis_type == 'none'].index, 'downstream_analysis_type'] = 'default'
    
    return project_df

def get_metadata(field, **kwargs):
    df = project_df
    for key, value in kwargs.items():
        df = df.loc[df.loc[:, key] == value]

    return df[field].to_list()[0]


def get_demux_indicator(wildcards):
    demux_dir = get_metadata(
        "demux_dir", sample_id=wildcards.sample, project_id=wildcards.project
    )

    return expand(demux_indicator, demux_dir=demux_dir)


def get_species_info(wildcards):
    # This function will return 3 things required by STAR:
    #    - annotation (.gtf file)
    #    - genome (.fa file)
    #    - index (a directory where the STAR index is)
    species = get_metadata(
        "species", project_id=wildcards.project, sample_id=wildcards.sample
    )

    return {
        "annotation": config["knowledge"]["annotations"][species],
        "genome": config["knowledge"]["genomes"][species],
        "index": config["knowledge"]["indices"][species]["star"],
    }


def get_rRNA_index(wildcards):
    species = get_metadata(
        "species", project_id=wildcards.project, sample_id=wildcards.sample
    )

    index = ""

    # return index only if it exists
    if "bt2_rRNA" in config["knowledge"]["indices"][species]:
        index = config["knowledge"]["indices"][species]["bt2_rRNA"]

    return {"rRNA_index": index}


def get_downstream_analysis_variables(project_id, sample_id):
    downstream_analysis_type = get_metadata('downstream_analysis_type',
            project_id = project_id,
            sample_id = sample_id)

    return config['downstream_analysis_variables'][downstream_analysis_type]

def get_dge_extra_params(wildcards):
    dge_type = wildcards.dge_type

    if dge_type == "_exon":
        return ""
    elif dge_type == "_intron":
        return "LOCUS_FUNCTION_LIST=null LOCUS_FUNCTION_LIST=INTRONIC"
    elif dge_type == "_all":
        return "LOCUS_FUNCTION_LIST=INTRONIC"
    if dge_type == "Reads_exon":
        return "OUTPUT_READS_INSTEAD=true"
    elif dge_type == "Reads_intron":
        return "OUTPUT_READS_INSTEAD=true LOCUS_FUNCTION_LIST=null LOCUS_FUNCTION_LIST=INTRONIC"
    elif dge_type == "Reads_all":
        return "OUTPUT_READS_INSTEAD=true LOCUS_FUNCTION_LIST=INTRONIC"


def get_basecalls_dir(wildcards):
    flowcell_id = get_metadata('flowcell_id', demux_dir = wildcards.demux_dir)

    if "basecall_folders" in config:
        for folder in config['basecall_folders']:
            bcl_folder = os.path.join(folder, flowcell_id)
            if os.path.isdir(bcl_folder):
                return [bcl_folder]
    # else return a fake path, which won't be present, so snakemake will fail for this, as input directory will be missing
    return ["none"] 

###############################
# Joining optical to illumina #
###############################


def get_sample_info(raw_folder):
    batches = os.listdir(raw_folder)

    df = pd.DataFrame(columns=["batch_id", "puck_id"])

    for batch in batches:
        batch_dir = microscopy_raw + "/" + batch

        puck_ids = os.listdir(batch_dir)

        df = df.append(
            pd.DataFrame({"batch_id": batch, "puck_id": puck_ids}), ignore_index=True
        )

    return df


###################
# Merging samples #
###################
def get_project(sample):
    # return the project id for a given sample id
    return project_df[project_df.sample_id.eq(sample)].project_id.to_list()[0]


def get_dropseq_final_bam(wildcards):
    # merged_name contains all the samples which should be merged,
    # separated by a dot each
    samples = config["samples_to_merge"][wildcards.merged_project][
        wildcards.merged_sample
    ]

    input_bams = []

    for sample in samples:
        input_bams = input_bams + expand(
            dropseq_final_bam, project=get_project(sample), sample=sample
        )
    return input_bams


def get_merged_bam_inputs(wildcards):
    # currently not used as we do not tag the bam files with the sample name
    samples = config["samples_to_merge"][wildcards.merged_project][
        wildcards.merged_sample
    ]

    input_bams = []

    for sample in samples:
        input_bams = input_bams + expand(
            sample_tagged_bam, merged_sample=wildcards.merged_name, sample=sample
        )

    return input_bams


def get_merged_star_log_inputs(wildcards):
    samples = config["samples_to_merge"][wildcards.merged_project][
        wildcards.merged_sample
    ]

    input_logs = []

    for sample in samples:
        input_logs = input_logs + expand(
            star_log_file, project=get_project(sample), sample=sample
        )

    return input_logs


def get_merged_ribo_depletion_log_inputs(wildcards):
    samples = config["samples_to_merge"][wildcards.merged_project][
        wildcards.merged_sample
    ]

    ribo_depletion_logs = []

    for sample in samples:
        ribo_depletion_logs = ribo_depletion_logs + expand(
            ribo_depletion_log, project=get_project(sample), sample=sample
        )

    return ribo_depletion_logs

def get_qc_sheet_parameters(project_id, sample_id):
    # returns a single row for a given sample_id
    # this will be the input of the parameters for the qc sheet parameter generation
    out_dict = project_df.loc[project_df.sample_id == sample_id]\
        .iloc[0]\
        .to_dict()

    out_dict["input_beads"] = str(get_downstream_analysis_variables(project_id, sample_id)['expected_n_beads'])

    return out_dict


def get_bt2_index(wildcards):
    species = get_metadata(
        "species", project_id=wildcards.project, sample_id=wildcards.sample
    )

    return config["knowledge"]["indices"][species]["bt2"]


def get_top_barcodes(wildcards):
    if wildcards.dge_cleaned == "":
        return {"top_barcodes": united_top_barcodes}
    else:
        return {'top_barcodes': united_top_barcodes_clean}

def get_dge_type(wildcards):
    downstream_analysis_type = get_metadata('downstream_analysis_type',
            project_id = wildcards.united_project,
            sample_id = wildcards.united_sample)

    if config['downstream_analysis_variables'][downstream_analysis_type]['clean_dge']:
        return {'dge_all_summary': dge_all_cleaned_summary, 'dge': dge_all_cleaned}
    else:
        return {'dge_all_summary': dge_all_summary, 'dge': dge_all}

def get_bam_tag_names(project_id, sample_id):
    barcode_flavor = get_metadata('barcode_flavor',
            project_id = project_id, sample_id = sample_id)

    bam_tags = config['knowledge']['barcode_flavor'][barcode_flavor]['bam_tags'] 

    tag_names = {}

    for tag in bam_tags.split(','):
        tag_name, tag_variable = tag.split(':')

        tag_names[tag_variable] = tag_name

    return tag_names

def get_puck_file(wildcards):
    puck_barcode_file = get_metadata('puck_barcode_file',
            project_id = wildcards.united_project,
            sample_id = wildcards.united_sample)

    if puck_barcode_file == 'none':
        return []
    else:
        return({'barcode_file': puck_barcode_file})
