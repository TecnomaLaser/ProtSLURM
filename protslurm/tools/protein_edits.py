'''Module to handle general edits to proteins, for example, adding or removing chains or ligands.'''
# imports
import json
import os

# dependencies
import pandas as pd

# customs
from protslurm import jobstarters
from protslurm.jobstarters import JobStarter
from protslurm.poses import Poses
from protslurm.residues import ResidueSelection
from protslurm.runners import Runner, col_in_df
from protslurm.config import PROTSLURM_ENV
from protslurm.config import AUXILIARY_RUNNER_SCRIPTS_DIR
from protslurm.utils.utils import parse_fasta_to_dict

class ChainAdder(Runner):
    '''Adds chains into proteins.'''
    def __init__(self, default_python=os.path.join(PROTSLURM_ENV, "python3"), jobstarter: JobStarter = None):
        self.python = self.search_path(default_python, "PROTSLURM_ENV")
        self.jobstarter = jobstarter

    def __str__(self):
        return "chain_adder"

    ################ Methods #########################
    def run(self, poses, prefix, jobstarter):
        raise NotImplementedError

    def add_chain(self, poses: Poses, prefix: str, ref_col: str, copy_chain: str, jobstarter: JobStarter = None, overwrite: bool = False) -> Poses:
        '''Simple method to add a chain into poses.'''
        # run superimpose without specifying anything to superimpose on (will not superimpose)
        chains_added = self.superimpose_add_chain(
            poses = poses,
            prefix=prefix,
            ref_col=ref_col,
            copy_chain=copy_chain,
            jobstarter=jobstarter,
            overwrite=overwrite
        )
        return chains_added

    def superimpose_add_chain(self, poses: Poses, prefix: str, ref_col: str, copy_chain: str, jobstarter: JobStarter = None, target_motif: ResidueSelection = None, reference_motif: ResidueSelection = None, target_chains: list = None, reference_chains: list = None, overwrite: bool = False) -> Poses:
        '''Method to add a protein chain after superimposition on a motif / chain.'''
        # sanity (motif and chain superimposition at the same time is not possible)
        def output_exists(work_dir, poses):
            '''checks if output of copying chains exists'''
            return os.path.isdir(work_dir) and all((os.path.isfile(os.path.join(work_dir, pose.rsplit("/", maxsplit=1)[-1])) for pose in poses.poses_list()))

        if (target_motif or reference_motif) and (target_chains or reference_chains):
            raise ValueError(f"Either motif or chains can be specified for superimposition, but never both at the same time! Decide whether to superimpose over a selected chain or a selected motif.")

        # runner setup
        script_path = f"{AUXILIARY_RUNNER_SCRIPTS_DIR}/add_chains_batch.py"
        work_dir, jobstarter = self.generic_run_setup(
            poses = poses,
            prefix = prefix,
            jobstarters = [jobstarter, self.jobstarter, poses.default_jobstarter]
        )

        # check for outputs
        if output_exists(work_dir, poses) and not overwrite:
            return poses.change_poses_dir(work_dir, copy=False)

        # setup motif args (extra function)
        input_dict = self.setup_superimposition_args(
            poses = poses,
            ref_col = ref_col,
            copy_chain = copy_chain,
            target_motif = target_motif,
            reference_motif = reference_motif,
            target_chains = target_chains,
            reference_chains = reference_chains,
        )

        # split input_dict into subdicts
        split_sublists = jobstarters.split_list(list(input_dict.keys()), n_sublists=jobstarter.max_cores)
        subdicts = [{target: input_dict[target] for target in sublist} for sublist in split_sublists]

        # write n=max_cores input_json files for add_chains_batch.py
        json_files = []
        for i, subdict in enumerate(subdicts, start=1):
            opts_json_p = f"{work_dir}/add_chain_input_{str(i).zfill(4)}.json"
            with open(opts_json_p, 'w', encoding="UTF-8") as f:
                json.dump(subdict, f)
            json_files.append(opts_json_p)

        # start add_chains_batch.py
        cmds = [f"{self.python} {script_path} --input_json {json_f} --output_dir {work_dir}" for json_f in json_files]
        jobstarter.start(
            cmds = cmds,
            jobname = f"add_chains_{prefix}",
            wait = True,
            output_path = work_dir
        )

        return poses.change_poses_dir(work_dir, copy=False)

    def setup_superimposition_args(self, poses: Poses, ref_col: str, copy_chain: str, target_motif: ResidueSelection = None, reference_motif: ResidueSelection = None, target_chains: list = None, reference_chains: list = None) -> dict:
        '''Prepares motif and chain specifications for superimposer setup.
        Returns dictionary (dict) that holds the kwargs for superimposition: {'target_motif': [target_motif_list], ...}'''
        # safety
        if (target_motif or reference_motif) and (target_chains or reference_chains):
            raise ValueError(f"Either motif or chains can be specified for superimposition, but not both!")

        # setup copy_chain and reference_pdb in output:
        col_in_df(poses.df, ref_col)
        copy_chain_l = setup_chain_list(copy_chain, poses)
        out_dict = {pose["poses"]: {"copy_chain": chain, "reference_pdb": os.path.abspath(pose[ref_col])} for pose, chain in zip(poses, copy_chain_l)}
        #out_dict = {'target_motif': None, 'reference_motif': None, 'target_chains': None, 'reference_chains': None}

        # if nothing is specified, return nothing.
        if all ((opt is None for opt in [reference_motif, target_motif, reference_chains, target_chains])):
            return out_dict

        # setup motif definitions
        if (target_motif or reference_motif):
            for pose in poses:
                out_dict[pose["poses"]]['target_motif'] = self.parse_motif(target_motif or reference_motif, pose)
                out_dict[pose["poses"]]['reference_motif'] = self.parse_motif(reference_motif or target_motif, pose)

        # setup chains definitions
        if (target_chains or reference_chains):
            for pose in poses:
                out_dict[pose["poses"]]["target_chains"] = parse_chain(target_chains or reference_chains, pose)
                out_dict[pose["poses"]]["reference_chains"] = parse_chain(reference_chains or target_chains, pose)

        return out_dict

    def parse_motif(self, motif: ResidueSelection|str, pose: pd.Series) -> str:
        '''Sets up motif from target_motif input.'''
        if isinstance(motif, ResidueSelection):
            return motif.to_string()
        if isinstance(motif, str):
            if motif in pose:
                # assumes motif is a column in pose (row in poses.df) that points to a ResidueSelection object
                return pose[motif].to_string()
            raise ValueError(f"If string is passed as motif, it has to be a column of the poses.df DataFrame. Otherwise pass a ResidueSelection object.")
        raise TypeError(f"Unsupportet parameter type for motif: {type(motif)} - Only ResidueSelection or str allowed!")

    def add_sequence(self, prefix: str, poses: Poses, seq: str = None, seq_col: str = None, sep: str = ":") -> None:
        '''Adds Sequence to pose in .fa format.'''
        poses.check_prefix(prefix)
        if not all(pose.endswith(".fa") or pose.endswith(".fasta") for pose in poses.poses_list()):
            raise ValueError(f"Poses must be .fasta files (.fa also fine)!")
        out_dir = f"{poses.work_dir}/prefix/"
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir, exist_ok=True)

        # prep seq input
        if seq and seq_col:
            raise ValueError(f"Either :seq: or :seq_col: can be passed to specify a sequence, but not both!")
        if seq:
            seqs = [seq for _ in poses]
        elif seq_col:
            col_in_df(poses.df, seq_col)
            seqs = poses.df[seq_col].to_list()
        else:
            raise ValueError(f"One of the parameters :seq: :seq_col: has to be passed to specify the sequence to add.")

        # separator (add sequence, or add protomer?)
        sep = "" if sep is None else sep

        # iterate over poses and add in sequence
        new_poses = []
        for pose, seq_ in zip(poses.poses_list(), seqs):
            # read fasta and add sequence.
            desc, orig_seq = list(parse_fasta_to_dict(pose).items())[0]
            orig_seq += sep + seq_

            # store at new location
            out_path = f"{out_dir}/{desc}.fa"
            with open(out_path, 'w', encoding="UTF-8") as f:
                f.write(f">{desc}\n{orig_seq}")
            new_poses.append(out_path)

        # update poses.df['poses'] to new location
        poses.change_poses_dir(out_dir, copy=False)

    def multimerize(self, prefix: str, poses: Poses, n_protomers: int, sep: str = ":") -> None:
        '''Takes .fa files from poses and makes multimers out of the sequences.
        :n_protomers: (int) specifies the number of protomers in the final .fa file.'''
        # setup directory and function
        poses.check_prefix(prefix)
        if not all(pose.endswith(".fa") or pose.endswith(".fasta") for pose in poses.poses_list()):
            raise ValueError(f"Poses must be .fasta files (.fa also fine)!")
        out_dir = f"{poses.work_dir}/prefix/"
        if not os.path.isdir(out_dir):
            os.makedirs(out_dir, exist_ok=True)

        # iterate over poses and add in sequence
        new_poses = []
        for pose in poses.poses_list():
            # read fasta and add sequence.
            desc, orig_seq = list(parse_fasta_to_dict(pose).items())[0]
            orig_seq += f"{sep}{orig_seq}" * (n_protomers - 1)

            # store at new location
            out_path = f"{out_dir}/{desc}.fa"
            with open(out_path, 'w', encoding="UTF-8") as f:
                f.write(f">{desc}\n{orig_seq}")
            new_poses.append(out_path)

        # update poses.df['poses'] to new location
        poses.change_poses_dir(out_dir, copy=False)

def setup_chain_list(chain_arg, poses: Poses) -> list[str]:
    '''Sets up chains for add_chains_batch.py'''
    if isinstance(chain_arg, str):
        if len(chain_arg) == 1:
            return [chain_arg for _ in poses]
        else:
            return [pose[chain_arg] for pose in poses]
    if isinstance(chain_arg, list) and len(chain_arg) == len(poses):
        return chain_arg
    raise ValueError(f"Inappropriate value for parameter :chain_arg:. Specify the chain (e.g. 'A'), the column where the chains are listed (e.g. 'chain_col') or give a list of chains the same length as poses.df (e.g. ['A', ...])")

def parse_chain(chain, pose: pd.Series) -> str:
    '''Sets up chain for add_chains_batch.py'''
    if isinstance(chain, str):
        return chain if len(chain) == 1 else pose[chain]
    raise TypeError(f"Inappropriate parameter type for parameter :chain: {type(chain)}. Only :str: allowed!")

class ChainRemover(Runner):
    '''Remove chains from poses.'''
    def __init__(self, default_python=os.path.join(PROTSLURM_ENV, "python3"), jobstarter: JobStarter = None):
        self.python = self.search_path(default_python, "PROTSLURM_ENV")
        self.jobstarter = jobstarter

    def __str__(self):
        return "chain_remover"

    #################################### METHODS #######################################
    def run(self, poses, prefix, jobstarter):
        raise NotImplementedError

    def remove_chains(self, poses: Poses, prefix: str, chains: list = None, jobstarter: JobStarter = None, overwrite: bool = False):
        '''Removes chains from poses.'''
        def output_exists(work_dir: str, files_list: list[str]) -> bool:
            '''checks if output of copying chains exists'''
            return os.path.isdir(work_dir) and all(os.path.isfile(fn) for fn in files_list)

        # setup runner
        script_path = f"{AUXILIARY_RUNNER_SCRIPTS_DIR}/remove_chains_batch.py"
        work_dir, jobstarter = self.generic_run_setup(
            poses = poses,
            prefix = prefix,
            jobstarters = [jobstarter, self.jobstarter, poses.default_jobstarter]
        )

        # define location of new poses:
        poses.df[f"{prefix}_location"] = [os.path.join(work_dir, os.path.basename(pose)) for pose in poses.poses_list()]

        # check if output is present
        if output_exists(work_dir, poses.df[f"{prefix}_location"].to_list()) and not overwrite:
            return poses.change_poses_dir(work_dir, copy=False)

        # setup chains
        if isinstance(chains, str):
            if len(chains) == 1:
                chain_list = [[chains] for _ in poses]
            else:
                self.check_for_prefix(chains, poses)
                chain_list = poses.df[chains].to_list()
        elif isinstance(chains, list):
            chain_list = [chains for _ in poses]

        # batch inputs to max_cores
        input_dict = {pose: chain for pose, chain in zip(poses.poses_list(), chain_list)}
        split_sublists = jobstarters.split_list(list(input_dict.keys()), n_sublists=jobstarter.max_cores)
        subdicts = [{target: input_dict[target] for target in sublist} for sublist in split_sublists]

        # write cmds
        json_files = []
        for i, subdict in enumerate(subdicts, start=1):
            opts_json_p = f"{work_dir}/remove_chain_input_{str(i).zfill(4)}.json"
            with open(opts_json_p, 'w', encoding="UTF-8") as f:
                json.dump(subdict, f)
            json_files.append(opts_json_p)

        # start remove_chains_batch.py
        cmds = [f"{self.python} {script_path} --input_json {json_f} --output_dir {work_dir}" for json_f in json_files]
        jobstarter.start(
            cmds = cmds,
            jobname = f"remove_chains_{prefix}",
            wait = True,
            output_path = work_dir
        )

        # reset poses location and return
        return poses.change_poses_dir(work_dir, copy=False)
