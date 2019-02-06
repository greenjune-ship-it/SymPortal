import os
import subprocess
import sys
from plumbum import local
import pandas as pd
from collections import defaultdict
from dbApp.models import DataSetSample
from general import (
    decode_utf8_binary_to_list, create_dict_from_fasta, create_seq_name_to_abundance_dict_from_name_file,
    decode_utf8_binary_to_list)
from pickle import dump, load
from multiprocessing import Queue, Manager, Process
from django import db

class BlastnAnalysis:
    def __init__(
            self, input_file_path, output_file_path,
            db_path='/home/humebc/phylogeneticSoftware/ncbi-blast-2.6.0+/ntdbdownload/nt', max_target_seqs=1,
            num_threads=1, output_format_string="6 qseqid sseqid staxids evalue pident qcovs staxid stitle ssciname",
            blastn_exec_path='blastn'
    ):

        self.input_file_path = input_file_path
        self.output_file_path = output_file_path
        self.db_path = db_path
        self.output_format_string = output_format_string
        self.max_target_seqs = max_target_seqs
        self.num_threads = num_threads
        self.blastn_exec_path = blastn_exec_path

    def execute(self, pipe_stdout_sterr=True):
        if pipe_stdout_sterr:
            completedProcess = subprocess.run([
                self.blastn_exec_path, '-out', self.output_file_path, '-outfmt', self.output_format_string, '-query',
                self.input_file_path, '-db', self.db_path,
                '-max_target_seqs', f'{self.max_target_seqs}', '-num_threads', f'{self.num_threads}'],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        else:
            completedProcess = subprocess.run([
                self.blastn_exec_path, '-out', self.output_file_path, '-outfmt', self.output_format_string, '-query',
                self.input_file_path, '-db', self.db_path,
                '-max_target_seqs', f'{self.max_target_seqs}', '-num_threads', f'{self.num_threads}'])
        return completedProcess

    def return_blast_output_as_list(self):
        return read_defined_file_to_list(self.output_format_string)

    def return_blast_results_dict(self):
        blast_output_file_as_list = self.return_blast_output_as_list()
        blast_output_dict = defaultdict(list)
        for line in blast_output_file_as_list:
            blast_output_dict[line.split('\t')[0]].append('\t'.join(line.split('\t')[1:]))
        return blast_output_file_as_list

class MothurAnalysis:

    def __init__(
            self, sequence_collection=None,  input_dir=None, output_dir=None, name=None,
            fastq_gz_fwd_path=None, fastq_gz_rev_path=None,
             name_file_path=None, mothur_execution_path='mothur', auto_convert_fastq_to_fasta=True,
            pcr_fwd_primer=None, pcr_rev_primer=None, pcr_oligo_file_path=None,
            pcr_fwd_primer_mismatch=2, pcr_rev_primer_mismatch=2, pcr_analysis_name=None, num_processors=10,
            stdout_and_sterr_to_pipe=True
            ):

        self.setup_core_attributes(auto_convert_fastq_to_fasta, fastq_gz_fwd_path, fastq_gz_rev_path,
                                   input_dir, mothur_execution_path, name, name_file_path, output_dir,
                                   sequence_collection, num_processors, stdout_and_sterr_to_pipe)


        self.setup_pcr_analysis_attributes(pcr_analysis_name, pcr_fwd_primer, pcr_fwd_primer_mismatch,
                                           pcr_oligo_file_path, pcr_rev_primer, pcr_rev_primer_mismatch)

    def setup_core_attributes(self, auto_convert_fastq_to_fasta, fastq_gz_fwd_path, fastq_gz_rev_path,
                              input_dir, mothur_execution_path, name, name_file_path, output_dir,
                              sequence_collection, num_processors, stdout_and_sterr_to_pipe):

        self.verify_that_is_either_sequence_collection_or_fastq_pair(fastq_gz_fwd_path, fastq_gz_rev_path,
                                                                 sequence_collection)

        if sequence_collection is not None:
            self.setup_sequence_collection_attribute(auto_convert_fastq_to_fasta, name, sequence_collection)
        elif sequence_collection is None:
            self.setup_fastq_attributes(fastq_gz_fwd_path, fastq_gz_rev_path)

        self.setup_remainder_of_core_attributes(input_dir, mothur_execution_path, name_file_path,
                                                output_dir, sequence_collection, num_processors, stdout_and_sterr_to_pipe)

    def setup_remainder_of_core_attributes(self, input_dir, mothur_execution_path, name_file_path,
                                           output_dir, sequence_collection, num_processors, stdout_and_sterr_to_pipe):
        self.exec_path = mothur_execution_path
        if input_dir is None:
            self.input_dir = os.path.dirname(sequence_collection.file_path)
        else:
            self.input_dir = input_dir
        if output_dir is None:
            self.output_dir = os.path.dirname(sequence_collection.file_path)
        else:
            self.output_dir = input_dir

        self.name_file_path = name_file_path
        self.mothur_batch_file_path = None
        self.processors = num_processors
        # we need to have seperate latest completed process objects for the actual commands and for the summaries
        # this is so that we can still extract useful information housed in the stdout from running the command
        # once the execute... function has been completed. Else, this information is lost due to it being replaced
        # by the completed_process of the summary that is automatically run after each command.
        self.latest_completed_process_command = None
        self.latest_completed_process_summary = None
        self.latest_summary_output_as_list = None
        self.latest_summary_path = None
        self.stdout_and_sterr_to_pipe = stdout_and_sterr_to_pipe

    def setup_fastq_attributes(self, fastq_gz_fwd_path, fastq_gz_rev_path):
        self.fastq_gz_fwd_path = fastq_gz_fwd_path
        self.fastq_gz_rev_path = fastq_gz_rev_path
        self.sequence_collection = None
        self.fasta_path = None

    def setup_sequence_collection_attribute(self, auto_convert_fastq_to_fasta, name, sequence_collection):
        self.fastq_gz_fwd_path = None
        self.fastq_gz_rev_path = None
        if sequence_collection.file_type == 'fastq':
            self.convert_to_fasta_or_raise_value_error(auto_convert_fastq_to_fasta, sequence_collection)
        if name is None:
            self.name = sequence_collection.name
        else:
            self.name = name
        self.sequence_collection = sequence_collection
        self.fasta_path = self.sequence_collection.file_path

    def convert_to_fasta_or_raise_value_error(self, auto_convert_fastq_to_fasta, sequence_collection):
        if auto_convert_fastq_to_fasta:
            print('SequenceCollection must be of type fasta\n. Running SeqeunceCollection.convert_to_fasta.\n')
            sequence_collection.convert_to_fasta()
        else:
            ValueError('SequenceCollection must be of type fasta. You can use the SequenceCollection')

    def verify_that_is_either_sequence_collection_or_fastq_pair(self, fastq_gz_fwd_path, fastq_gz_rev_path,
                                                            sequence_collection):
        if sequence_collection and (fastq_gz_fwd_path or fastq_gz_rev_path):
            raise ValueError(
                'Please create a MothurAnalysis from either a sequence_collection OR a pair of fastq_gz files.\n'
                'MothurAnalysis.from_pair_of_fastq_gz_files or MothurAnalysis.from_sequence_collection')

    def setup_pcr_analysis_attributes(self, pcr_analysis_name, pcr_fwd_primer, pcr_fwd_primer_mismatch,
                                      pcr_oligo_file_path, pcr_rev_primer, pcr_rev_primer_mismatch):
        if pcr_analysis_name:
            if pcr_analysis_name.lower() in ['symvar', 'sym_var']:
                self.pcr_fwd_primer = 'GAATTGCAGAACTCCGTGAACC'
                self.rev_primer = 'GAATTGCAGAACTCCGTGAACC',

            elif pcr_analysis_name.lower() in ['laj', 'lajeunesse']:
                self.pcr_fwd_primer = 'GAATTGCAGAACTCCGTG'
                self.pcr_rev_primer = 'CGGGTTCWCTTGTYTGACTTCATGC'
            else:
                raise ValueError(
                    'pcr_analysis_name \'{}\' is not recognised.\nOptions are \'symvar\' or \'lajeunesse\'.'
                )
        else:
            self.pcr_fwd_primer = pcr_fwd_primer
            self.pcr_rev_primer = pcr_rev_primer
        self.pcr_fwd_primer_mismatch = pcr_fwd_primer_mismatch
        self.pcr_rev_primer_mismatch = pcr_rev_primer_mismatch
        self.pcr_oligo_file_path = pcr_oligo_file_path

    # init class methods for the MothurAnalysis
    @classmethod
    def from_pair_of_fastq_gz_files(cls, name, fastq_gz_fwd_path, fastq_gz_rev_path,
                                    output_dir=None, mothur_execution_string='mothur', num_processors=10,
                                    stdout_and_sterr_to_pipe=True
                                    ):
        return cls(name=name, sequence_collection=None, mothur_execution_path=mothur_execution_string,
                   input_dir=os.path.dirname(os.path.abspath(fastq_gz_fwd_path)), output_dir=output_dir,
                   fastq_gz_fwd_path=fastq_gz_fwd_path, fastq_gz_rev_path=fastq_gz_rev_path,
                   name_file_path=None, num_processors=num_processors,
                   stdout_and_sterr_to_pipe=stdout_and_sterr_to_pipe)

    @classmethod
    def from_sequence_collection(cls, sequence_collection, name=None, input_dir=None,
                                 output_dir=None, mothur_execution_path='mothur',
                                 pcr_fwd_primer=None, pcr_rev_primer=None, pcr_oligo_file_path=None,
                                 pcr_fwd_primer_mismatch=2, pcr_rev_primer_mismatch=2, pcr_analysis_name=None,
                                 num_processors=10, stdout_and_sterr_to_pipe=True):
        return cls(
            name=name, sequence_collection=sequence_collection, input_dir=input_dir,
            output_dir=output_dir, mothur_execution_path=mothur_execution_path, pcr_fwd_primer=pcr_fwd_primer,
            pcr_rev_primer=pcr_rev_primer, pcr_oligo_file_path=pcr_oligo_file_path,
            pcr_fwd_primer_mismatch=pcr_fwd_primer_mismatch, pcr_rev_primer_mismatch=pcr_rev_primer_mismatch,
            pcr_analysis_name=pcr_analysis_name, num_processors=num_processors,
            stdout_and_sterr_to_pipe=stdout_and_sterr_to_pipe
        )

    # ########################################

    # main mothur commands
    def execute_screen_seqs(self, argument_dictionary):
        """This will perform a mothur screen.seqs.
        Because there are so many arguments taht the screen seqs command can take we will use a dictionary
        to determine how the mothur batch file should be made. The dictionary is very simple: the key should
        be the argument and the value should be the value. e.g.
        argument_dictionary = {'max_length':'500', 'min_length':'100'}
        """
        self.__screen_seqs_make_and_write_mothur_batch_file(argument_dictionary)
        self.__run_mothur_batch_file_command()
        good_fasta_path = self.__screen_seqs_extract_good_output_path()
        self.fasta_path = good_fasta_path
        self.__update_sequence_collection_from_fasta_file()
        self.__execute_summary()

    def execute_pcr(self, do_reverse_pcr_as_well=False):
        """This will perform a mothur pcr.seqs analysis.
        if do_reverse_pcr__as_well is true then we will also reverse complement the fasta a perform the
        """

        self.__pcr_validate_attributes_are_set()

        self.__pcr_make_and_write_oligo_file_if_doesnt_exist()

        self.__pcr_make_and_write_mothur_batch_file()

        self.__run_mothur_batch_file_command()

        fwd_output_scrapped_fasta_path, fwd_output_good_fasta_path = self.__pcr_extract_good_and_scrap_output_paths()

        remove_primer_mismatch_annotations_from_fasta(fwd_output_scrapped_fasta_path)
        remove_primer_mismatch_annotations_from_fasta(fwd_output_good_fasta_path)


        # then we should clean up the output_bad_fasta
        # then reverse complement it
        # then do a pcr on it again using the same oligo set as the first run
        # we should then get the output from that pcr and add it to the previous run
        if do_reverse_pcr_as_well:
            self.fasta_path = fwd_output_scrapped_fasta_path
            self.__rev_comp_make_and_write_mothur_batch_file()
            self.__run_mothur_batch_file_command()
            self.fasta_path = self.__extract_output_path_first_line()
            self.__pcr_make_and_write_mothur_batch_file()
            self.__run_mothur_batch_file_command()
            rev_output_good_fasta_path = self.__pcr_extract_good_and_scrap_output_paths()[1]
            remove_primer_mismatch_annotations_from_fasta(rev_output_good_fasta_path)
            self.__make_new_fasta_path_for_fwd_rev_combined(rev_output_good_fasta_path)
            # now create a fasta that is the good fasta from both of the pcrs. this will become the new mothuranalysis fasta.

            combine_two_fasta_files(
                path_one=fwd_output_good_fasta_path,
                path_two=rev_output_good_fasta_path,
                path_for_combined=self.fasta_path
            )
        else:
            self.fasta_path = fwd_output_good_fasta_path
        if self.name_file_path:
            self.__update_sequence_collection_from_fasta_name_pair()
        else:
            self.__update_sequence_collection_from_fasta_file()

    def execute_make_contigs(self):
        """
        This will use the fastq_gz_fwd_path and fastq_gz_rev_paths to make a .file file that will be used
        as input to mothurs make.contigs command.
        N.B. Although in theory we can use the fastq_gz_fwd_path and the rev path directly as arguments to the mothur.contigs
        there appears to be a bug that doesn't allow this to work. Using a .file file is fine though. The .file file
        is in the format "path_to_file_1 path_to_file_2" i.e the paths only separated by a space.
        :return:
        """
        # create .file file for the fwd fastq pair and the reverse fastq pair
        dot_file_file_path = self.__make_contig_make_and_write_out_dot_file()

        self.__make_contig_make_and_write_mothur_batch(dot_file_file_path)

        self.__run_mothur_batch_file_command()

        self.fasta_path = self.__extract_output_path_first_line()

        self.__update_sequence_collection_from_fasta_file()
        # now run the summary
        self.__execute_summary()

    def execute_unique_seqs(self):

        self.__unique_seqs_make_and_write_mothur_batch()

        self.__run_mothur_batch_file_command()

        self.name_file_path, self.fasta_path = self.__extract_output_path_two_lines()

        self.__update_sequence_collection_from_fasta_name_pair()

        self.__execute_summary()


    def execute_split_abund(self, abund_cutoff=2):

        self.__split_abund_make_and_write_mothur_batch(abund_cutoff)

        self.__run_mothur_batch_file_command()

        self.name_file_path, self.fasta_path = self.__split_abund_extract_output_path_name_and_fasta()

        self.__update_sequence_collection_from_fasta_name_pair()

        self.__execute_summary()

    def __execute_summary(self):
        self.__summarise_make_and_write_mothur_batch()
        self.__run_mothur_batch_file_command()
        self.latest_summary_path = self.__extract_output_path_first_line()
        self.latest_summary_output_as_list = decode_utf8_binary_to_list(self.latest_completed_process_command.stdout)

    # #####################

    def __split_abund_extract_output_path_name_and_fasta(self):
        stdout_string_as_list = decode_utf8_binary_to_list(self.latest_completed_process_command.stdout)
        for i in range(len(stdout_string_as_list)):
            print(stdout_string_as_list[i])
            if 'Output File Names' in stdout_string_as_list[i]:
                return stdout_string_as_list[i + 2], stdout_string_as_list[i + 4]

    def __split_abund_make_and_write_mothur_batch(self, abund_cutoff):
        if self.name_file_path:
            mothur_batch_file = [
                f'set.dir(input={self.input_dir})',
                f'set.dir(output={self.output_dir})',
                f'split.abund(fasta={self.fasta_path}, name={self.name_file_path}, cutoff={abund_cutoff})'
            ]
        else:
            raise RuntimeError(
                'Non name_file_path present. '
                'A name file is necessary to be able to assess the abundances of sequences in the .fasta file'
            )
        self.mothur_batch_file_path = os.path.join(self.input_dir, 'mothur_batch_file')
        write_list_to_destination(self.mothur_batch_file_path, mothur_batch_file)

    def __unique_seqs_make_and_write_mothur_batch(self):
        if self.name_file_path:
            mothur_batch_file = [
                f'set.dir(input={self.input_dir})',
                f'set.dir(output={self.output_dir})',
                f'unique.seqs(fasta={self.fasta_path}, name={self.name_file_path})'
            ]
        else:
            mothur_batch_file = [
                f'set.dir(input={self.input_dir})',
                f'set.dir(output={self.output_dir})',
                f'unique.seqs(fasta={self.fasta_path})'
            ]
        self.mothur_batch_file_path = os.path.join(self.input_dir, 'mothur_batch_file')
        write_list_to_destination(self.mothur_batch_file_path, mothur_batch_file)

    def __summarise_make_and_write_mothur_batch(self):
        if self.name_file_path:
            mothur_batch_file = [
                f'set.dir(input={self.input_dir})',
                f'set.dir(output={self.output_dir})',
                f'summary.seqs(fasta={self.fasta_path})'
            ]
        else:
            mothur_batch_file = [
                f'set.dir(input={self.input_dir})',
                f'set.dir(output={self.output_dir})',
                f'summary.seqs(fasta={self.fasta_path}, name={self.name_file_path})'
            ]
        self.mothur_batch_file_path = os.path.join(self.input_dir, 'mothur_batch_file')
        write_list_to_destination(self.mothur_batch_file_path, mothur_batch_file)


    def __make_contig_make_and_write_mothur_batch(self, dot_file_file_path):
        mothur_batch_file = [
            f'set.dir(input={self.input_dir})',
            f'set.dir(output={self.output_dir})',
            f'make.contigs(file={dot_file_file_path})'
        ]
        self.mothur_batch_file_path = os.path.join(self.input_dir, 'mothur_batch_file')
        write_list_to_destination(self.mothur_batch_file_path, mothur_batch_file)

    def __make_contig_make_and_write_out_dot_file(self):
        dot_file_file = [f'{self.fastq_gz_fwd_path} {self.fastq_gz_rev_path}']
        dot_file_file_path = os.path.join(self.input_dir, 'fastq_pair.file')
        write_list_to_destination(dot_file_file_path, dot_file_file)
        return dot_file_file_path


    def __make_new_fasta_path_for_fwd_rev_combined(self, rev_output_good_fasta_path):
        self.fasta_path = rev_output_good_fasta_path.replace('.scrap.pcr.rc.pcr', '.pcr.combined')

    def __update_sequence_collection_from_fasta_file(self):
        self.sequence_collection.set_list_of_nucleotide_sequences_from_fasta_or_fastq(self.fasta_path)

    def __update_sequence_collection_from_fasta_name_pair(self):
        self.sequence_collection.generate_sequence_collection_from_fasta_name_pair(self.fasta_path)

    def __rev_comp_make_and_write_mothur_batch_file(self):
        mothur_batch_file = self.__make_rev_complement_mothur_batch_file()
        self.mothur_batch_file_path = os.path.join(self.input_dir, 'mothur_batch_file')
        write_list_to_destination(self.mothur_batch_file_path, mothur_batch_file)

    def __make_rev_complement_mothur_batch_file(self):
        mothur_batch_file = [
            f'set.dir(input={self.input_dir})',
            f'set.dir(output={self.output_dir})',
            f'reverse.seqs(fasta={self.fasta_path})'
        ]
        return mothur_batch_file

    def __extract_output_path_first_line(self):
        stdout_string_as_list = decode_utf8_binary_to_list(self.latest_completed_process_command.stdout)
        for i in range(len(stdout_string_as_list)):
            print(stdout_string_as_list[i])
            if 'Output File Names' in stdout_string_as_list[i]:
                return stdout_string_as_list[i+1]

    def __extract_output_path_two_lines(self):
        stdout_string_as_list = decode_utf8_binary_to_list(self.latest_completed_process_command.stdout)
        for i in range(len(stdout_string_as_list)):
            print(stdout_string_as_list[i])
            if 'Output File Names' in stdout_string_as_list[i]:
                return stdout_string_as_list[i + 1], stdout_string_as_list[i + 2]

    def __pcr_extract_good_and_scrap_output_paths(self):
        stdout_string_as_list = decode_utf8_binary_to_list(self.latest_completed_process_command.stdout)
        for i in range(len(stdout_string_as_list)):
            print(stdout_string_as_list[i])
            if 'Output File Names' in stdout_string_as_list[i]:
                output_good_fasta_path = stdout_string_as_list[i + 1]
                output_scrapped_fasta_path = stdout_string_as_list[i + 3]
                return output_scrapped_fasta_path, output_good_fasta_path

    def __screen_seqs_extract_good_output_path(self):
        stdout_string_as_list = decode_utf8_binary_to_list(self.latest_completed_process_command.stdout)
        for i in range(len(stdout_string_as_list)):
            print(stdout_string_as_list[i])
            if 'Output File Names' in stdout_string_as_list[i]:
                return stdout_string_as_list[i + 1]

    def __run_mothur_batch_file_command(self):
        if self.stdout_and_sterr_to_pipe:
            self.latest_completed_process_command = subprocess.run(
                [self.exec_path, self.mothur_batch_file_path],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
        else:
            self.latest_completed_process_command = subprocess.run(
                [self.exec_path, self.mothur_batch_file_path])

    def __run_mothur_batch_file_summary(self, stdout_and_sterr_to_pipe=False):
        if stdout_and_sterr_to_pipe:
            self.latest_completed_process_summary = subprocess.run(
                [self.exec_path, self.mothur_batch_file_path],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE
            )
        else:
            self.latest_completed_process_summary = subprocess.run(
                [self.exec_path, self.mothur_batch_file_path])

    def __pcr_make_and_write_mothur_batch_file(self):
        mothur_batch_file = self.__pcr_make_mothur_batch_file()
        self.mothur_batch_file_path = os.path.join(self.input_dir, 'mothur_batch_file')
        write_list_to_destination(self.mothur_batch_file_path, mothur_batch_file)

    def __screen_seqs_make_and_write_mothur_batch_file(self, argument_dictionary):
        mothur_batch_file = self.__screen_seqs_make_mothur_batch_file(argument_dictionary)
        self.mothur_batch_file_path = os.path.join(self.input_dir, 'mothur_batch_file')
        write_list_to_destination(self.mothur_batch_file_path, mothur_batch_file)

    def __screen_seqs_create_additional_arguments_string(self, argument_dict):
        individual_argument_strings = []
        for k, v in argument_dict.items():
            if v is not None:
                individual_argument_strings.append(f'{k}={v}')
        return ', '.join(individual_argument_strings)

    def __screen_seqs_make_mothur_batch_file(self, argument_dict):
        additional_arguments_string = self.__screen_seqs_create_additional_arguments_string(argument_dict)
        if self.name_file_path:
            mothur_batch_file = [
                f'set.dir(input={self.input_dir})',
                f'set.dir(output={self.output_dir})',
                f'screen.seqs(fasta={self.fasta_path}, name={self.name_file_path}, {additional_arguments_string})'
            ]

        else:
            mothur_batch_file = [
                f'set.dir(input={self.input_dir})',
                f'set.dir(output={self.output_dir})',
                f'screen.seqs(fasta={self.fasta_path}, {additional_arguments_string})'
            ]
        return mothur_batch_file

    def __pcr_make_mothur_batch_file(self):
        if self.name_file_path:
            mothur_batch_file = [
                f'set.dir(input={self.input_dir})',
                f'set.dir(output={self.output_dir})',
                f'pcr.seqs(fasta={self.fasta_path}, name={self.name_file_path}, oligos={self.pcr_oligo_file_path}, '
                f'pdiffs={self.pcr_fwd_primer_mismatch}, rdiffs={self.pcr_rev_primer_mismatch}, processors={self.processors})'
            ]

        else:
            mothur_batch_file = [
                f'set.dir(input={self.input_dir})',
                f'set.dir(output={self.output_dir})',
                f'pcr.seqs(fasta={self.fasta_path}, oligos={self.pcr_oligo_file_path}, '
                f'pdiffs={self.pcr_fwd_primer_mismatch}, rdiffs={self.pcr_rev_primer_mismatch}, processors={self.processors})'
            ]
        return mothur_batch_file

    def __pcr_make_and_write_oligo_file_if_doesnt_exist(self):
        if self.pcr_oligo_file_path is None:
            oligo_file = [
                f'forward\t{self.pcr_fwd_primer}',
                f'reverse\t{self.pcr_rev_primer}'
            ]
            self.pcr_oligo_file_path = os.path.join(self.input_dir, 'oligo_file.oligo')
            write_list_to_destination(self.pcr_oligo_file_path, oligo_file)

    def __pcr_validate_attributes_are_set(self):
        sys.stdout.write(f'\nValidating PCR attributes are set\n')
        if self.fasta_path is None:
            raise RuntimeError('Fasta_path is None. A valid fasta_path is required to perform the pcr method.')
        if self.pcr_fwd_primer is None or self.pcr_rev_primer is None:
            if self.pcr_fwd_primer is None and self.pcr_rev_primer is None:
                raise RuntimeError('Please set fwd_primer and rev_primer: ')
            elif self.pcr_fwd_primer is None:
                raise RuntimeError('Please set fwd_primer.')
            elif self.pcr_rev_primer is None:
                raise RuntimeError('Please set fwd_primer.')
        sys.stdout.write(f'\nPCR attributes: OK\n')

class SequenceCollection:
    """ A sequence collection is a set of sequences either generated from a fastq file or from a fasta file.
    It cannot be created directly from binary files or from paired files. As such, to generate a SequenceCollection
    for example from a pair of fastaq.gz files, you would first have to run a mothur contig analysis and create
    the SeqeunceCollection from the resultant fasta file that is generated."""
    def __init__(self, name, path_to_file=None, auto_convert_to_fasta=True):
        self.name = name
        self.file_path = path_to_file
        # self.file_as_list = read_defined_file_to_list(self.file_path)
        self.file_type = self.infer_file_type()
        self.list_of_nucleotide_sequences = None
        self.set_list_of_nucleotide_sequences_from_fasta_or_fastq()
        if auto_convert_to_fasta:
            self.convert_to_fasta()


    def convert_to_fasta(self):
        self.file_path = self.write_out_as_fasta()
        self.file_type = 'fasta'

    def __len__(self):
        return(len(self.list_of_nucleotide_sequences))

    def write_out_as_fasta(self, path_for_fasta_file = None):
        if self.file_type == 'fasta':
            print(f'SequenceCollection is already of type fasta and a fasta file already exists: {self.file_path}')
            return
        if self.file_type == 'fastq':
            if path_for_fasta_file is None:
                fasta_path = self.infer_fasta_path_from_current_fastq_path()
                write_list_to_destination(destination=fasta_path, list_to_write=self.as_fasta())
            else:
                fasta_path = path_for_fasta_file
                write_list_to_destination(destination=fasta_path, list_to_write=self.as_fasta())
            return fasta_path


    def infer_fasta_path_from_current_fastq_path(self):
        return self.file_path.replace('fastq', 'fasta')

    def set_list_of_nucleotide_sequences_from_fasta_or_fastq(self, alt_fasta_path=None):
        """This will generate a list of NucleotideSequence objects.
        It will do this with a fasta or fastq file as the sole input.
        As such no abundance data will be collected for each of the NucleotideSequence objects."""
        if self.file_type == 'fasta':
            self.parse_fasta_file_and_extract_nucleotide_sequence_objects(alternative_fasta_file_path=alt_fasta_path)
        elif self.file_type == 'fastq':
            self.parse_fastq_file_and_extract_nucleotide_sequence_objects()

    def generate_sequence_collection_from_fasta_name_pair(self, name_file_path, fasta_file_path):
        """This will generate a list of NucleotideSequence objects.
        It will do this with a fasta or fastq file as the sole input.
        As such no abundance data will be collected for each of the NucleotideSequence objects."""
        list_of_nucleotide_sequence_objects = []
        fasta_dict = create_dict_from_fasta(fasta_path=fasta_file_path)
        seq_name_to_abundace_dict = create_seq_name_to_abundance_dict_from_name_file(name_file_path)
        for seq_name, seq_sequence in fasta_dict.items():
            list_of_nucleotide_sequence_objects.append(
                NucleotideSequence(sequence=seq_sequence, name=seq_name, abundance=seq_name_to_abundace_dict[seq_name])
            )
        self.list_of_nucleotide_sequences = list_of_nucleotide_sequence_objects

    def parse_fasta_file_and_extract_nucleotide_sequence_objects(self, alternative_fasta_file_path=None):
        list_of_nucleotide_sequence_objects = []
        if alternative_fasta_file_path:
            self.file_path = alternative_fasta_file_path
        fasta_file = read_defined_file_to_list(self.file_path)
        for i in range(0, len(fasta_file), 2):
            list_of_nucleotide_sequence_objects.append(
                NucleotideSequence(sequence=fasta_file[i+1], name=fasta_file[i][1:])
            )
        self.list_of_nucleotide_sequences = list_of_nucleotide_sequence_objects

    def parse_fastq_file_and_extract_nucleotide_sequence_objects(self):
        list_of_nuleotide_sequence_objects = []
        fastq_file_as_list = read_defined_file_to_list(self.file_path)
        for i in range(len(fastq_file_as_list)):
            if i < len(fastq_file_as_list) - 2:
                if self.is_fastq_defline(fastq_file_as_list, i):
                    self.create_new_nuc_seq_object_and_add_to_list(fastq_file_as_list, i, list_of_nuleotide_sequence_objects)
        self.list_of_nucleotide_sequences = list_of_nuleotide_sequence_objects

    def is_fastq_defline(self, fastsq_file, index_value):
        if fastsq_file[index_value].startswith('@') and fastsq_file[index_value + 2][0] == '+':
            return True

    def create_new_nuc_seq_object_and_add_to_list(self, fastq_file_as_list, index_val, list_of_nuleotide_sequence_objects):
        name, sequence = self.get_single_fastq_info_from_fastq_file_by_index(fastq_file_as_list, index_val)
        list_of_nuleotide_sequence_objects.append(NucleotideSequence(sequence=sequence, name=name))

    def get_single_fastq_info_from_fastq_file_by_index(self, fastq_file_as_list, index_val):
        name = fastq_file_as_list[index_val][1:].split(' ')[0]
        sequence = fastq_file_as_list[index_val + 1]
        return name, sequence

    def infer_file_type(self):
        if 'fasta' in self.file_path:
            return 'fasta'
        elif 'fastq' in self.file_path:
            return 'fastq'
        else:
            raise ValueError('Input file used to create the SequenceCollection must be either fasta or fastq')

    def as_fasta(self):
        fasta_file = []
        for seq_obj in self.list_of_nucleotide_sequences:
            fasta_file.extend([f'>{seq_obj.name}', f'{seq_obj.sequence}'])
        return fasta_file

class NucleotideSequence:
    def __init__(self, sequence, name=None, abundance=None):
        self.sequence = sequence
        self.length = len(sequence)
        self.name = name
        self.abundance = abundance

class InitialMothurWorker:
    def __init__(self, contig_pair, data_set_object, temp_wkd, debug):
        self.sample_name = contig_pair.split('\t')[0].replace('[dS]', '-')
        self.data_set_sample = DataSetSample.objects.get(
                name=self.sample_name, data_submission_from=data_set_object
            )
        self.cwd = os.path.join(temp_wkd, self.sample_name)
        os.makedirs(self.cwd, exist_ok=True)
        self.pre_med_seq_dump_dir = self.cwd.replace('tempData', 'pre_MED_seqs')
        os.makedirs(self.pre_med_seq_dump_dir, exist_ok=True)
        self.mothur_analysis_object = MothurAnalysis(
            pcr_analysis_name='symvar',
            input_dir=self.cwd,
            output_dir=self.cwd,
            fastq_gz_fwd_path=contig_pair.split('\t')[1],
            fastq_gz_rev_path=contig_pair.split('\t')[2],
            stdout_and_sterr_to_pipe=debug)
        self.debug = debug

    def execute(self):
        sys.stdout.write(f'{self.sample_name}: QC started\n')

        self.do_make_contigs()

        self.set_absolute_num_seqs_after_make_contigs()

        self.do_screen_seqs()

        self.do_unique_seqs()

        self.do_split_abund()

        self.do_unique_seqs()

        self.do_fwd_and_rev_pcr()

        self.do_unique_seqs()

        self.set_unique_num_seqs_after_initial_qc()

        self.set_absolute_num_seqs_after_inital_qc()

        self.save_changes_to_data_set_sample()

        sys.stdout.write(f'{self.sample_name}: Initial mothur complete\n')

        self.write_out_final_name_and_fasta_for_tax_screening()


    def write_out_final_name_and_fasta_for_tax_screening(self):
        name_file_as_list = read_defined_file_to_list(self.mothur_analysis_object.name_file_path)
        taxonomic_screening_name_file_path = os.path.join(self.cwd, 'name_file_for_tax_screening.names')
        write_list_to_destination(taxonomic_screening_name_file_path, name_file_as_list)
        fasta_file_as_list = read_defined_file_to_list(self.mothur_analysis_object.fasta_path)
        taxonomic_screening_fasta_file_path = os.path.join(self.cwd, 'fasta_file_for_tax_screening.fasta')
        write_list_to_destination(taxonomic_screening_fasta_file_path, fasta_file_as_list)

    def save_changes_to_data_set_sample(self):
        self.data_set_sample.save()

    def do_fwd_and_rev_pcr(self):
        self.mothur_analysis_object.execute_pcr(do_reverse_pcr_as_well=True)
        self.check_for_no_seqs_after_pcr_and_raise_runtime_error()

    def do_split_abund(self):
        self.mothur_analysis_object.execute_split_abund(abund_cutoff=2)
        self.check_for_error_and_raise_runtime_error()

    def do_unique_seqs(self):
        self.mothur_analysis_object.execute_unique_seqs()
        self.check_for_error_and_raise_runtime_error()

    def do_screen_seqs(self):
        self.mothur_analysis_object.execute_screen_seqs(argument_dictionary={'maxambig': 0, 'maxhomop': 5})
        self.check_for_error_and_raise_runtime_error()

    def do_make_contigs(self):
        self.mothur_analysis_object.execute_make_contigs()
        self.check_for_error_and_raise_runtime_error()

    def set_absolute_num_seqs_after_make_contigs(self):
        number_of_contig_seqs_absolute = len(
            read_defined_file_to_list(self.mothur_analysis_object.latest_summary_path)
        ) - 1
        self.data_set_sample.num_contigs = number_of_contig_seqs_absolute
        sys.stdout.write(
            f'{self.sample_name}: data_set_sample_instance_in_q.num_contigs = {number_of_contig_seqs_absolute}\n')

    def set_unique_num_seqs_after_initial_qc(self):
        number_of_contig_seqs_unique = len(
            read_defined_file_to_list(self.mothur_analysis_object.latest_summary_path)) - 1
        self.data_set_sample.post_qc_unique_num_seqs = number_of_contig_seqs_unique
        sys.stdout.write(
            f'{self.sample_name}: '
            f'data_set_sample_instance_in_q.post_qc_unique_num_seqs = {number_of_contig_seqs_unique}\n')

    def set_absolute_num_seqs_after_inital_qc(self):
        last_summary = read_defined_file_to_list(self.mothur_analysis_object.latest_summary_path)
        absolute_count = 0
        for line in last_summary[1:]:
            absolute_count += int(line.split('\t')[6])
        self.data_set_sample.post_qc_absolute_num_seqs = absolute_count
        sys.stdout.write(
            f'{self.sample_name}: data_set_sample_instance_in_q.post_qc_absolute_num_seqs = {absolute_count}\n')

    def check_for_no_seqs_after_pcr_and_raise_runtime_error(self):
        if len(self.mothur_analysis_object.sequence_collection) == 0:
            self.log_qc_error_and_continue(errorreason='No seqs left after PCR')
            raise RuntimeError(sample_name=self.sample_name)

    def check_for_error_and_raise_runtime_error(self):
        for stdout_line in decode_utf8_binary_to_list(
                self.mothur_analysis_object.latest_completed_process_command.stdout
        ):
            if '[WARNING]: Blank fasta name, ignoring read.' in stdout_line:
                self.log_qc_error_and_continue(errorreason='Blank fasta name')
                raise RuntimeError(sample_name=self.sample_name)
            if 'ERROR' in stdout_line:
                self.log_qc_error_and_continue(errorreason='error in inital QC')
                raise RuntimeError(sample_name=self.sample_name)

    def log_qc_error_and_continue(self, errorreason):
        print('Error in processing sample: {}'.format(self.sample_name))
        self.data_set_sample.unique_num_sym_seqs = 0
        self.data_set_sample.absolute_num_sym_seqs = 0
        self.data_set_sample.initial_processing_complete = True
        self.data_set_sample.error_in_processing = True
        self.data_set_sample.error_reason = errorreason
        self.save_changes_to_data_set_sample()

class TaxonomicScreeningHandler:
    def __init__(self, samples_that_caused_errors_in_qc_list, checked_samples_list, list_of_samples_names, num_proc):
        self.input_queue = Queue()
        self.manager = Manager()
        self.sub_evalue_sequence_to_num_sampes_found_in_mp_dict = self.manager.dict()
        self.sub_evalue_nucleotide_sequence_to_clade_mp_dict = self.manager.dict()
        self.error_samples_mp_list = self.manager.list(samples_that_caused_errors_in_qc_list)
        self.checked_samples_mp_list = self.manager.list(checked_samples_list)
        self.list_of_sample_names = list_of_samples_names
        self.num_proc = num_proc
        self.load_input_queue()

    def load_input_queue(self):
        # load up the input q
        for sample_name in self.list_of_sample_names:
            self.input_queue.put(sample_name)
        # load in the STOPs
        for n in range(self.num_proc):
            self.input_queue.put('STOP')



class TaxonomicScreeningWorker:
    def __init__(
            self, sample_name, wkd, path_to_symclade_db, debug, e_val_collection_mp_dict,
            checked_samples_mp_list, sub_evalue_nucleotide_sequence_to_clade_mp_dict):
        self.sample_name = sample_name
        self.cwd = os.path.join(wkd, self.sample_name)
        self.fasta_file_path = os.path.join(self.cwd, 'fasta_file_for_tax_screening.fasta')
        self.fasta_dict = create_dict_from_fasta(fasta_path=self.fasta_file_path)
        self.name_file_path = os.path.join(self.cwd, 'name_file_for_tax_screening.names')
        self.name_dict = {a.split('\t')[0]: a for a in read_defined_file_to_list(self.name_file_path)}
        self.path_to_symclade_db = path_to_symclade_db
        self.debug = debug
        # This is a managed dictionary where key is a nucleotide sequence that has:
        # 1 - provided a match in the blast analysis
        # 2 - is of suitable size
        # 3 - but has an evalue match below the cuttof
        # the value of the dict is an int that represents how many samples this nucleotide sequence was found in
        self.e_val_collection_mp_dict = e_val_collection_mp_dict
        # This dictionary will be used outside of the multiprocessing to append the clade of a given sequences
        # that is being added to the symClade reference database
        self.sub_evalue_nucleotide_sequence_to_clade_mp_dict = sub_evalue_nucleotide_sequence_to_clade_mp_dict
        self.non_symbiodinium_sequences_list = []
        self.sequence_name_to_clade_dict = None

        self.blast_output_as_list = None
        self.already_processed_blast_seq_result = []
        # this is a managed list that holds the names of samples from which no sequences were thrown out from
        # it will be used in downstream processes.
        self.checked_samples_mp_list = checked_samples_mp_list

    def execute(self):
        sys.stdout.write(f'{self.sample_name}: verifying seqs are Symbiodinium and determining clade\n')

        blastn_analysis = BlastnAnalysis(
            input_file_path=self.fasta_file_path,
            output_file_path=os.path.join(self.cwd, 'blast.out'), db_path=self.path_to_symclade_db,
            output_format_string="6 qseqid sseqid staxids evalue pident qcovs")

        if self.debug:
            blastn_analysis.execute(pipe_stdout_sterr=False)
        else:
            blastn_analysis.execute(pipe_stdout_sterr=True)

        sys.stdout.write(f'{self.sample_name}: BLAST complete\n')

        self.blast_output_as_list = blastn_analysis.return_blast_output_as_list()

        self.if_debug_warn_if_blast_out_empty_or_low_seqs()

        self.sequence_name_to_clade_dict = {
            blast_out_line.split('\t')[0]: blast_out_line.split('\t')[1][-1] for blast_out_line in self.blast_output_as_list}

        self.add_seqs_with_no_blast_match_to_non_sym_list()

        # NB blast results sometimes return several matches for the same seq.
        # as such we will use the already_processed_blast_seq_resulst to make sure that we only
        # process each sequence once.
        self.identify_and_allocate_non_sym_and_sub_e_seqs()

        if not self.non_symbiodinium_sequences_list:
            self.checked_samples_mp_list.append(self.sample_name)

        self.pickle_out_objects_to_cwd_for_use_in_next_worker()

    def pickle_out_objects_to_cwd_for_use_in_next_worker(self):
        dump(self.name_dict, open(f'{self.cwd}/name_dict.pickle', 'wb'))
        dump(self.fasta_dict, open(f'{self.cwd}/fasta_dict.pickle', 'wb'))
        dump(self.non_symbiodinium_sequences_list, open(f'{self.cwd}/throw_away_seqs.pickle', 'wb'))
        dump(self.sequence_name_to_clade_dict, open(f'{self.cwd}/blast_dict.pickle', 'wb'))

    def identify_and_allocate_non_sym_and_sub_e_seqs(self):
        for line in self.blast_output_as_list:
            name_of_current_sequence = line.split('\t')[0]
            if name_of_current_sequence in self.already_processed_blast_seq_result:
                continue
            self.already_processed_blast_seq_result.append(name_of_current_sequence)
            identity = float(line.split('\t')[4])
            coverage = float(line.split('\t')[5])

            # noinspection PyPep8,PyBroadException
            # here we are looking for sequences to add to the non_symbiodinium_sequence_list
            # if a sequence fails at any of our if statements it will be added to the non_symbiodinium_sequence_list
            try:
                evalue_power = int(line.split('\t')[3].split('-')[1])
                # With the smallest sequences i.e. 185bp it is impossible to get above the 100 threshold
                # even if there is an exact match. As such, we sould also look at the match identity and coverage
                if evalue_power < 100:  # evalue cut off, collect sequences that don't make the cut
                    self.if_ident_cov_size_good_add_seq_to_non_sym_list_and_eval_dict(
                        coverage, identity, name_of_current_sequence
                    )
            except:
                # here we weren't able to extract the evalue_power for some reason.
                self.if_ident_cov_size_good_add_seq_to_non_sym_list_and_eval_dict(
                    coverage, identity, name_of_current_sequence
                )

    def if_ident_cov_size_good_add_seq_to_non_sym_list_and_eval_dict(self, coverage, identity, name_of_current_sequence):
        """This method will add nucleotide sequences that gave blast matches but that were below the evalue
        or indentity and coverage thresholds to the evalue collection dict and
        record how many samples that sequence was found in.
        It will also take into account the size thresholds that would normally happen later in the code during further mothur qc.
        Finally, it will also populate a nucleotide sequence to clade dictionary that will be used outside of
        the MPing to append the clade of a given sequences that is being added to the symClade reference database.
        """
        if identity < 80 or coverage < 95:
            self.non_symbiodinium_sequences_list.append(name_of_current_sequence)
            # incorporate the size cutoff here that would normally happen in the further mothur qc later in the code
            if 184 < len(self.fasta_dict[name_of_current_sequence]) < 310:
                if self.fasta_dict[name_of_current_sequence] in self.e_val_collection_mp_dict.keys():
                    self.e_val_collection_mp_dict[self.fasta_dict[name_of_current_sequence]] += 1
                else:
                    self.e_val_collection_mp_dict[self.fasta_dict[name_of_current_sequence]] = 1
                    self.sub_evalue_nucleotide_sequence_to_clade_mp_dict[
                        self.fasta_dict[name_of_current_sequence]
                    ] = self.sequence_name_to_clade_dict[name_of_current_sequence]

    def add_seqs_with_no_blast_match_to_non_sym_list(self):
        sequences_with_no_blast_match_as_set = set(self.fasta_dict.keys()) - set(self.sequence_name_to_clade_dict.keys())
        self.non_symbiodinium_sequences_list.extend(list(sequences_with_no_blast_match_as_set))
        sys.stdout.write(
            f'{self.sample_name}: {len(sequences_with_no_blast_match_as_set)} sequences thrown out '
            f'initially due to being too divergent from reference sequences\n')

    def if_debug_warn_if_blast_out_empty_or_low_seqs(self):
        if self.debug:
            if not self.blast_output_as_list:
                print(f'WARNING blast output file is empty for {self.sample_name}')
            else:
                if len(self.blast_output_as_list) < 10:
                    print(
                        f'WARNING blast output file for {self.sample_name} '
                        f'is only {len(self.blast_output_as_list)} lines long')


def return_list_of_file_names_in_directory(directory_to_list):
    """
    return a list that contains the filenames found in the specified directory
    :param directory_to_list: the directory that the file names should be returned from
    :return: list of strings that are the file names found in the directory_to_list
    """
    list_of_file_names_in_directory = []
    for (dirpath, dirnames, filenames) in os.walk(directory_to_list):
        list_of_file_names_in_directory.extend(filenames)
        return list_of_file_names_in_directory

def return_list_of_file_paths_in_directory(directory_to_list):
    """
    return a list that contains the full paths of each of the files found in the specified directory
    :param directory_to_list: the directory that the file paths should be returned from
    :return: list of strings that are the file paths found in the directory_to_list
    """
    list_of_file_paths_in_directory = []
    for (dirpath, dirnames, filenames) in os.walk(directory_to_list):
        list_of_file_paths_in_directory.extend([os.path.join(directory_to_list, file_name) for file_name in filenames])
        return list_of_file_paths_in_directory

def return_list_of_directory_names_in_directory(directory_to_list):
    """
        return a list that contains the directory names found in the specified directory
        :param directory_to_list: the directory that the directory names should be returned from
        :return: list of strings that are the directory names found in the directory_to_list
        """
    list_of_directory_names_in_directory = []
    for (dirpath, dirnames, filenames) in os.walk(directory_to_list):
        list_of_directory_names_in_directory.extend(dirnames)
        return list_of_directory_names_in_directory


def return_list_of_directory_paths_in_directory(directory_to_list):
    """
        return a list that contains the full paths of each of the directories found in the specified directory
        :param directory_to_list: the directory that the directory paths should be returned from
        :return: list of strings that are the directory paths found in the directory_to_list
        """
    list_of_directory_paths_in_directory = []
    for (dirpath, dirnames, filenames) in os.walk(directory_to_list):
        list_of_directory_paths_in_directory.extend([os.path.join(directory_to_list, dir_name) for dir_name in dirnames])
        return list_of_directory_paths_in_directory

def read_defined_file_to_list(filename):
    with open(filename, mode='r') as reader:
        return [line.rstrip() for line in reader]


def read_defined_file_to_generator(filename):
    with open(filename, mode='r') as reader:
        return (line.rstrip() for line in reader)


def write_list_to_destination(destination, list_to_write):
    #print('Writing list to ' + destination)
    try:
        os.makedirs(os.path.dirname(destination))
    except FileExistsError:
        pass

    with open(destination, mode='w') as writer:
        i = 0
        while i < len(list_to_write):
            if i != len(list_to_write)-1:
                writer.write(list_to_write[i] + '\n')
            elif i == len(list_to_write)-1:
                writer.write(list_to_write[i])
            i += 1


def remove_primer_mismatch_annotations_from_fasta(fasta_path):
    temp_fasta = []
    fasta_to_clean = read_defined_file_to_list(fasta_path)
    for i in range(len(fasta_to_clean) - 1):
        if fasta_to_clean[i]:
            if fasta_to_clean[i][0] == '>' and fasta_to_clean[i + 1]:
                if '|' in fasta_to_clean[i]:
                    temp_fasta.extend([fasta_to_clean[i].split('|')[0], fasta_to_clean[i + 1]])
                else:
                    temp_fasta.extend([fasta_to_clean[i].split('\t')[0], fasta_to_clean[i+1]])
    write_list_to_destination(fasta_path, temp_fasta)



def create_no_space_fasta_file(fasta_list):
    temp_list = []
    i = 0
    while i < len(fasta_list):
        temp_list.extend([fasta_list[i].split('\t')[0], fasta_list[i + 1]])
        i += 2
    return temp_list

def combine_two_fasta_files(path_one, path_two, path_for_combined):
    one_file_one = read_defined_file_to_list(path_one)
    one_file_two = read_defined_file_to_list(path_two)
    one_file_one.extend(one_file_two)
    write_list_to_destination(path_for_combined, one_file_one)

def mafft_align_fasta(input_path, output_path, method='auto', mafft_exec_string='mafft', num_proc=1, iterations=1000):
    # TODO add an algorythm argument so that the particular style of alignemtn can be chosen
    # http://plumbum.readthedocs.io/en/latest/local_commands.html#pipelining
    print(f'Aligning {input_path}')
    if method == 'auto':
        mafft = local[f'{mafft_exec_string}']
        (mafft['--auto', '--thread', f'{num_proc}', input_path] > output_path)()
    if method == 'linsi':
        mafft = local[f'{mafft_exec_string}']
        (mafft['--localpair', '--maxiterate', f'{iterations}', '--thread', f'{num_proc}', input_path] > output_path)()
    print(f'Writing to {output_path}')

def fasta_to_pandas_df(fasta_as_list):
    temp_df = pd.DataFrame([list(line) for line in fasta_as_list if not line.startswith('>')])
    seq_names = [line[1:] for line in fasta_as_list if line.startswith('>')]
    temp_df.index=seq_names
    return temp_df

def pandas_df_to_fasta(cropped_fasta_df):
    temp_fasta = []
    for ind in cropped_fasta_df.index.tolist():
        temp_fasta.extend(['>{}'.format(ind), ''.join(list(cropped_fasta_df.loc[ind]))])
    return temp_fasta

def convert_interleaved_to_sequencial_fasta(fasta_as_list):
    new_fasta = []
    temp_seq_string_list = []
    for i, fasta_line in enumerate(fasta_as_list):
        if fasta_line.startswith('>'):
            if temp_seq_string_list:
                new_fasta.append(''.join(temp_seq_string_list))
                temp_seq_string_list = []
                new_fasta.append(fasta_line)
            else:
                new_fasta.append(fasta_line)
        else:
            temp_seq_string_list.append(fasta_line)
    new_fasta.append(''.join(temp_seq_string_list))
    return new_fasta

def remove_gaps_from_fasta(fasta_as_list):
    gapless_fasta = []
    for fasta_line in fasta_as_list:
        if fasta_line.startswith('>'):
            gapless_fasta.append(fasta_line)
        else:
            gapless_fasta.append(fasta_line.replace('-', ''))
    return gapless_fasta