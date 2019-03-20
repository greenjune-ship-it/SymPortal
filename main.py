#!/usr/bin/env python3.6
""" SymPortal: a novel analytical framework and platform for coral algal
    symbiont next-generation sequencing ITS2 profiling
    Copyright (C) 2018  Benjamin C C Hume

    This program is free software: you can redistribute it and/or modify
    it under the terms of the GNU General Public License as published by
    the Free Software Foundation, either version 3 of the License, or
    (at your option) any later version.

    This program is distributed in the hope that it will be useful,
    but WITHOUT ANY WARRANTY; without even the implied warranty of
    MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
    GNU General Public License for more details.

    You should have received a copy of the GNU General Public License
    along with this program.  If not, see
    https://github.com/didillysquat/SymPortal_framework/tree/master/LICENSE.txt.
    TODO Integrate the VirtualObjectsManager and specifically VirtualDataSetSample and VirtualCladeCollections
    into the DataLoading and then into the SequenceCountTableCreator.

    """



# Django specific settings
import os
from datetime import datetime
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
from django.conf import settings
# ####### Setup Django DB and Models ########
# Ensure settings are read
from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
# Your application specific imports
from dbApp.models import DataSet, DataAnalysis
############################################


import output
import plotting
import sys
import distance
import argparse
import data_loading
import sp_config
import data_analysis
import pickle

class SymPortalWorkFlowManager:
    # INIT
    def __init__(self, custom_args_list=None):
        self.args = self._define_args(custom_args_list)
        # general attributes
        self.symportal_root_directory = os.path.abspath(os.path.dirname(__file__))
        self.date_time_str = str(datetime.now()).replace(' ', '_').replace(':', '-')
        self.submitting_user = sp_config.user_name
        self.submitting_user_email = sp_config.user_email

        # for data_loading
        self.data_set_object = None
        self.screen_sub_eval_bool = None
        if sp_config.system_type == 'remote':
            self.screen_sub_eval_bool = True
        else:
            self.screen_sub_eval_bool = False
        self.reference_db = 'symClade.fa'
        self.output_seq_count_table_obj = None
        # for data analysis
        self.within_clade_cutoff = 0.03
        self.data_analysis_object = None
        self.sp_data_analysis = None
        self.output_type_count_table_obj = None
        self.type_stacked_bar_plotter = None

        # for dist and pcoa outputs

        # Variable that will hold the plotting class object
        self.distance_object = None

    def _define_args(self, custom_args_list=None):
        parser = argparse.ArgumentParser(
            description='Intragenomic analysis of the ITS2 region of the nrDNA',
            epilog='For support email: benjamin.hume@kaust.edu.sa')
        group = parser.add_mutually_exclusive_group(required=True)
        self._define_mutually_exclusive_args(group)
        self._define_additional_args(group, parser)
        if custom_args_list is not None:
            return parser.parse_args(custom_args_list)
        else:
            return parser.parse_args()

    def _define_additional_args(self, group, parser):
        parser.add_argument('--num_proc', type=int, help='Number of processors to use', default=1)
        parser.add_argument('--name', help='A name for your input or analysis', default='noName')
        parser.add_argument('--description', help='An optional description', default='No description')
        parser.add_argument('--data_analysis_id', type=int, help='The ID of the data_analysis you wish to output from')
        group.add_argument(
            '--vacuum_database', action='store_true',
            help='Vacuuming the database will free up memory from objects that have been deleted recently')
        parser.add_argument('--bootstrap', type=int, help='Number of bootstrap iterations to perform', default=100)
        parser.add_argument(
            '--data_sheet',
            help='An absolute path to the .xlsx file containing the meta-data information for the data_set\'s samples')
        parser.add_argument('--no_figures', action='store_true', help='Skip figure production')
        parser.add_argument('--no_ordinations', action='store_true', help='Skip ordination analysis')
        parser.add_argument('--debug', action='store_true', help='Present additional stdout output', default=False)

        parser.add_argument(
            '--no_output', action='store_true', help='Do no output: count tables, figures, ordinations', default=False)

        parser.add_argument(
            '--distance_method',
            help='Either \'unifrac\' or \'braycurtis\', default=braycurtis. The method to use when '
                 'calculating distances between its2 type profiles or samples.', default='braycurtis')
        # when run as remote
        parser.add_argument(
            '--submitting_user_name',
            help='Only for use when running as remote\nallows the association of a different user_name to the '
                 'data_set than the one listed in sp_config', default='not supplied')

        parser.add_argument(
            '--submitting_user_email',
            help='Only for use when running as remote\nallows the association of a different user_email to the data_set '
                 'than the one listed in sp_config', default='not supplied')

    def _define_mutually_exclusive_args(self, group):
        group.add_argument(
            '--load', metavar='path_to_dir',
            help='Run this to load data to the framework\'s database. The first argument to this command must be an '
                 'absolute path to a directory containing  the paired sequencing reads in .fastq.gz format. Alternatively, '
                 'this path can point directly to a single compressed file containing the same paired fastq.gz files. '
                 '\nA name must be associated with the data_set using the --name flag. \nThe number of processes to use '
                 'can also be specified using the --num_proc flag. \nA datasheet can also be uploaded using the '
                 '--data_sheet flag and the full path to the .xlsx data_sheet file (RECOMMENDED). \n'
                 'To skip the generation of figures pass the --no_figures flag.\n To skip the generation of '
                 'ordination files (pairwise distances and PCoA coordinates) pass the --no_ordinations flag')
        group.add_argument(
            '--analyse', metavar='DataSet UIDs',
            help='Analyse one or more data_set objects together. Enter comma separated UIDs of the data_set uids you '
                 'which to analyse. e.g.: 43,44,45. If you wish to use all available dataSubmissions, you may pass '
                 '\'all\' as an argument. To display all data_sets currently submitted to the framework\'s database, '
                 'including their ids, use the \'show_data_sets\' command\nTo skip the generation of figures pass the '
                 '--no_figures flag.\nTo skip the generation of ordination files (pairwise distances and PCoA coordinates) '
                 'pass the --no_ordinations flag')
        group.add_argument(
            '--display_data_sets', action='store_true', help='Display data_sets currently in the framework\'s database')
        group.add_argument(
            '--display_analyses', action='store_true',
            help=' Display data_analysis objects currently stored in the framework\'s database')
        group.add_argument(
            '--print_output_seqs', metavar='DataSet UIDs',
            help='Use this function to output ITS2 sequence count tables for given data_set instances')
        group.add_argument(
            '--print_output_seqs_sample_set', metavar='DataSetSample UIDs',
            help='Use this function to output ITS2 sequence count tables for a collection of DataSetSample instances. '
                 'The input to this function should be a comma separated string of the UIDs of the DataSetSample instances '
                 'in question. e.g. 345,346,347,348')
        group.add_argument(
            '--print_output_types', metavar='DataSet UIDs, DataAnalysis UID',
            help='Use this function to output the ITS2 sequence and ITS2 type profile count tables for a given set of '
                 'data_sets that have been run in a given analysis. Give the data_set uids that you wish to make outputs '
                 'for as arguments to the --print_output_types flag. To output for multiple data_set objects, '
                 'comma separate the uids of the data_set objects, e.g. 44,45,46. Give the ID of the analysis you wish to '
                 'output these from using the --data_analysis_id flag.\nTo skip the generation of figures pass the '
                 '--no_figures flag.')
        group.add_argument(
            '--print_output_types_sample_set', metavar='DataSet UIDs, DataAnalysis UID',
            help='Use this function to output the ITS2 sequence and ITS2 type profile count tables for a given set of '
                 'DataSetSample objects that have been run in a given DataAnalysis. Give the DataSetSample '
                 'UIDs that you wish to make outputs from as arguments to the --print_output_types flag. To output for '
                 'multiple DataSetSample objects, comma separate the UIDs of the DataSetSample objects, '
                 'e.g. 5644,5645,5646. Give the UID of the DataAnalysis you wish to output these from using the '
                 '--data_analysis_id flag.\nTo skip the generation of figures pass the '
                 '--no_figures flag.')
        group.add_argument(
            '--between_type_distances', metavar='DataSetSample UIDs, DataAnalysis UID',
            help='Use this function to output UniFrac pairwise distances between ITS2 type profiles clade separated')
        group.add_argument(
            '--between_type_distances_sample_set', metavar='DataSetSample UIDs, DataAnalysis UID',
            help='Use this function to output pairwise distances between ITS2 type profiles clade '
                 'separated from a given collection of DataSetSample objects')
        group.add_argument(
            '--between_sample_distances', metavar='DataSetSample UIDs',
            help='Use this function to output pairwise distances between samples clade separated from a '
                 'given collection of DataSet objects')
        group.add_argument(
            '--between_sample_distances_sample_set', metavar='DataSetSample UIDs',
            help='Use this function to output pairwise distances between samples clade '
                 'separated from a given collection of DataSetSample objects')

    def start_work_flow(self):
        if self.args.load:
            self.perform_data_loading()
        elif self.args.analyse:
            self._perform_data_analysis()

        # Output data
        elif self.args.print_output_seqs:
            self.perform_stand_alone_sequence_output()
        elif self.args.print_output_seqs_sample_set:
            self.perform_stand_alone_sequence_output()
        elif self.args.print_output_types:
            self.perform_stand_alone_type_output()
        elif self.args.print_output_types_sample_set:
            self.perform_stand_alone_type_output()

        # Distances
        elif self.args.between_type_distances:
            self.perform_type_distance_stand_alone()
        elif self.args.between_type_distances_sample_set:
            self.perform_type_distance_stand_alone()
        elif self.args.between_sample_distances:
            self._perform_sample_distance_stand_alone()
        elif self.args.between_sample_distances_sample_set:
            self._perform_sample_distance_stand_alone()

        # DB display functions
        elif self.args.display_data_sets:
            self.perform_display_data_sets()
        elif self.args.display_analyses:
            self.perform_display_analysis_types()
        elif self.args.vacuumDatabase:
            self.perform_vacuum_database()

    # GENERAL
    def _plot_if_not_too_many_samples(self, plotting_function, num_samples=None, max_num_samples=1000):
        if num_samples is None:
            num_samples = self.number_of_samples
        if num_samples < max_num_samples:
            plotting_function()
        else:
            print('Too many samples (num_samples) to plot.')

    def _set_data_analysis_obj_from_arg_analysis_uid(self):
        self.data_analysis_object = DataAnalysis.objects.get(id=self.args.data_analysis_id)

    def _verify_data_analysis_uid_provided(self):
        if not self.args.data_analysis_id:
            raise RuntimeError(
                'Please provide a data_analysis to ouput from by providing a data_analysis '
                'ID to the --data_analysis_id flag. To see a list of data_analysis objects in the '
                'framework\'s database, use the --display_analyses flag.')

    def _plot_sequence_stacked_bar_from_seq_output_table(self):
        """Plot up the sequence abundances from the output sequence count table. NB this is in the
        case where we have not run an analysis in conjunction, i.e. there are no ITS2 type profiles to consider.
        As such, no ordered list of DataSetSamples should be passed to the plotter."""
        self.seq_stacked_bar_plotter = plotting.SeqStackedBarPlotter(
            output_directory=self.output_seq_count_table_obj.output_dir,
            seq_relative_abund_count_table_path=self.output_seq_count_table_obj.path_to_seq_output_df_relative,
            time_date_str=self.output_seq_count_table_obj.time_date_str)
        self.seq_stacked_bar_plotter.plot_stacked_bar_seqs()

    def _plot_type_stacked_bar_from_type_output_table(self):
        self.type_stacked_bar_plotter = plotting.TypeStackedBarPlotter(
            output_directory=self.output_type_count_table_obj.output_dir,
            type_relative_abund_count_table_path=self.output_type_count_table_obj.path_to_relative_count_table,
            time_date_str=self.output_type_count_table_obj.date_time_str)
        self.type_stacked_bar_plotter.plot_stacked_bar_seqs()

    def _plot_type_distances_from_distance_object(self):
        """Search for the path of the .csv file that holds the PC coordinates and pass this into plotting"""
        sys.stdout.write('\nPlotting ITS2 type profile distances\n')
        for pcoa_path in [path for path in self.distance_object.output_file_paths if path.endswith('.csv')]:
            local_plotter = plotting.DistScatterPlotterTypes(
                csv_path=pcoa_path, date_time_str=self.data_analysis_object.time_stamp)
            local_plotter.make_type_dist_scatter_plot()

    def _plot_sample_distances_from_distance_object(self):
        """Search for the path of the .csv file that holds the PC coordinates and pass this into plotting"""
        sys.stdout.write('\nPlotting sample distances\n')
        for pcoa_path in [path for path in self.distance_object.output_file_paths if path.endswith('.csv')]:
            local_plotter = plotting.DistScatterPlotterSamples(
                csv_path=pcoa_path, date_time_str=self.data_analysis_object.time_stamp)
            local_plotter.make_sample_dist_scatter_plot()

    # DATA ANALYSIS
    def _perform_data_analysis(self):
        self._verify_name_arg_given()
        self.create_new_data_analysis_obj()
        self._start_data_analysis()

        if not self.args.no_output:
            self._do_data_analysis_output()
            if not self.args.no_ordinations:
                self._do_data_analysis_ordinations()
            else:
                print('Ordinations skipped at user\'s request')
        else:
            print('\nOutputs skipped at user\'s request\n')

    def _start_data_analysis(self):
        # Perform the analysis
        self.sp_data_analysis = data_analysis.SPDataAnalysis(
            workflow_manager_parent=self, data_analysis_obj=self.data_analysis_object)
        self.sp_data_analysis.analyse_data()

    def _do_data_analysis_output(self):
        self._make_data_analysis_output_type_tables()
        self._make_data_analysis_output_seq_tables()
        self.number_of_samples = len(self.output_type_count_table_obj.sorted_list_of_vdss_uids_to_output)

        if not self.args.no_figures:
            #
            self._plot_if_not_too_many_samples(self._plot_type_stacked_bar_from_type_output_table)

            self._plot_if_not_too_many_samples(self._plot_sequence_stacked_bar_with_ordered_dss_uids_from_type_output)
        else:
            print('\nFigure plotting skipped at user\'s request')

    def _plot_sequence_stacked_bar_with_ordered_dss_uids_from_type_output(self):
        """Plot the sequence abundance info from the output sequence count table ensuring to take in the same
        DataSetSample order as that used in the ITS2 type profile output that was conducted in parallel."""
        self.seq_stacked_bar_plotter = plotting.SeqStackedBarPlotter(
            output_directory=self.output_seq_count_table_obj.output_dir,
            seq_relative_abund_count_table_path=self.output_seq_count_table_obj.path_to_seq_output_df_relative,
            ordered_sample_uid_list=self.output_type_count_table_obj.sorted_list_of_vdss_uids_to_output,
            time_date_str=self.output_seq_count_table_obj.time_date_str)
        self.seq_stacked_bar_plotter.plot_stacked_bar_seqs()

    def _do_data_analysis_ordinations(self):
        self._perform_data_analysis_type_distances()
        if not self.args.no_figures:
            self._plot_if_not_too_many_samples(self._plot_type_distances_from_distance_object)

        self._perform_data_analysis_sample_distances()
        if not self.args.no_figures:
            self._plot_if_not_too_many_samples(self._plot_sample_distances_from_distance_object)

    def _perform_data_analysis_sample_distances(self):
        if self.args.distance_method:
            if self.args.distance_method == 'unifrac':
                self._start_analysis_unifrac_sample_distances()
            else:  # braycurtis
                self._start_analysis_braycurtis_sample_distances()
        else:
            self._start_analysis_braycurtis_sample_distances()

    def _start_analysis_unifrac_sample_distances(self):
        self.distance_object = distance.SampleUnifracDistPCoACreator(
            symportal_root_directory=self.symportal_root_directory,
            num_processors=self.args.num_proc, call_type='analysis',
            date_time_string=self.data_analysis_object.time_stamp,
            data_set_sample_uid_list=self.output_type_count_table_obj.sorted_list_of_vdss_uids_to_output,
            output_dir=self.output_type_count_table_obj.output_dir)
        self.distance_object.compute_unifrac_dists_and_pcoa_coords()

    def _start_analysis_braycurtis_sample_distances(self):
        self.distance_object = distance.SampleBrayCurtisDistPCoACreator(
            symportal_root_directory=self.symportal_root_directory,
            call_type='analysis',
            date_time_string=self.data_analysis_object.time_stamp,
            data_set_sample_uid_list=self.output_type_count_table_obj.sorted_list_of_vdss_uids_to_output,
            output_dir=self.output_type_count_table_obj.output_dir)
        self.distance_object.compute_braycurtis_dists_and_pcoa_coords()

    def _perform_data_analysis_type_distances(self):
        if self.args.distance_method:
            if self.args.distance_method == 'unifrac':
                self._start_analysis_unifrac_type_distances()
            else:  # braycurtis
                self._start_analysis_braycurtis_type_distances()
        else:
            self._start_analysis_braycurtis_type_distances()

    def _start_analysis_unifrac_type_distances(self):
        self.distance_object = distance.TypeUnifracDistPCoACreator(
            symportal_root_directory=self.symportal_root_directory,
            num_processors=self.args.num_proc, call_type='analysis',
            data_analysis_obj=self.data_analysis_object,
            date_time_string=self.data_analysis_object.time_stamp,
            data_set_uid_list=self.output_type_count_table_obj.sorted_list_of_vdss_uids_to_output,
            output_dir=self.output_type_count_table_obj.output_dir)
        self.distance_object.compute_unifrac_dists_and_pcoa_coords()

    def _start_analysis_braycurtis_type_distances(self):
        self.distance_object = distance.TypeBrayCurtisDistPCoACreator(
            symportal_root_directory=self.symportal_root_directory,
            call_type='analysis',
            data_analysis_obj=self.data_analysis_object,
            date_time_string=self.data_analysis_object.time_stamp,
            data_set_sample_uid_list=self.output_type_count_table_obj.sorted_list_of_vdss_uids_to_output,
            output_dir=self.output_type_count_table_obj.output_dir)
        self.distance_object.compute_braycurtis_dists_and_pcoa_coords()

    def _make_data_analysis_output_seq_tables(self):
        self.output_seq_count_table_obj = output.SequenceCountTableCreator(
            call_type='analysis',
            num_proc=self.args.num_proc,
            symportal_root_dir=self.symportal_root_directory,
            ds_uids_output_str=self.data_analysis_object.list_of_data_set_uids,
            output_dir=self.output_type_count_table_obj.output_dir,
            sorted_sample_uid_list=self.output_type_count_table_obj.sorted_list_of_vdss_uids_to_output,
            analysis_obj=self.data_analysis_object,
            time_date_str=self.output_type_count_table_obj.date_time_str)
        self.output_seq_count_table_obj.make_output_tables()

    def _make_data_analysis_output_type_tables(self):
        # Write out the AnalysisType count table
        self.output_type_count_table_obj = output.OutputTypeCountTable(
            call_type='analysis', num_proc=self.args.num_proc,
            symportal_root_directory=self.symportal_root_directory,
            within_clade_cutoff=self.within_clade_cutoff,
            data_set_uids_to_output=self.sp_data_analysis.list_of_data_set_uids,
            virtual_object_manager=self.sp_data_analysis.virtual_object_manager,
            data_analysis_obj=self.sp_data_analysis.data_analysis_obj)
        self.output_type_count_table_obj.output_types()

    def create_new_data_analysis_obj(self):
        self.data_analysis_object = DataAnalysis(
            list_of_data_set_uids=self.args.analyse, within_clade_cutoff=self.within_clade_cutoff,
            name=self.args.name, time_stamp=self.date_time_str,
            submitting_user=self.submitting_user, submitting_user_email=self.submitting_user_email)
        self.data_analysis_object.description = self.args.description
        self.data_analysis_object.save()

    # DATA LOADING
    # TODO push the creation of data set sample object back until the submission has been verified
    def perform_data_loading(self):
        self._verify_name_arg_given()
        self.make_new_dataset_object()
        self._execute_data_loading()

    def _execute_data_loading(self):
        data_loading_object = data_loading.DataLoading(
            data_set_object=self.data_set_object, datasheet_path=self.args.data_sheet, user_input_path=self.args.load,
            screen_sub_evalue=self.screen_sub_eval_bool, num_proc=self.args.num_proc, no_fig=self.args.no_figures,
            no_ord=self.args.no_ordinations, distance_method=self.args.distance_method, debug=self.args.debug)
        data_loading_object.load_data()
        return data_loading_object

    def _verify_name_arg_given(self):
        if self.args.name == 'noName':
            sys.exit('Please provide a name using the --name flag. e.g. --name informative_name')

    def make_new_dataset_object(self):
        self.data_set_object = DataSet(
            name=self.args.name, time_stamp=self.date_time_str, reference_fasta_database_used=self.reference_db,
            submitting_user=self.submitting_user, submitting_user_email=self.submitting_user_email)
        self.data_set_object.save()

    # STAND_ALONE SEQUENCE OUTPUT
    def perform_stand_alone_sequence_output(self):
        if self.args.print_output_seqs_sample_set:
            self._stand_alone_sequence_output_data_set_sample()
        else:
            self._stand_alone_sequence_output_data_set()
        self.number_of_samples = len(self.output_seq_count_table_obj.sorted_sample_uid_list)
        self._plot_if_not_too_many_samples(self._plot_sequence_stacked_bar_from_seq_output_table)

    def _stand_alone_sequence_output_data_set(self):
        self.output_seq_count_table_obj = output.SequenceCountTableCreator(
            symportal_root_dir=self.symportal_root_directory, call_type='stand_alone',
            ds_uids_output_str=self.args.print_output_seqs,
            num_proc=self.args.num_proc)
        self.output_seq_count_table_obj.make_output_tables()

    def _stand_alone_sequence_output_data_set_sample(self):
        self.output_seq_count_table_obj = output.SequenceCountTableCreator(
            symportal_root_dir=self.symportal_root_directory, call_type='stand_alone',
            dss_uids_output_str=self.args.print_output_seqs_sample_set,
            num_proc=self.args.num_proc)
        self.output_seq_count_table_obj.make_output_tables()

    # STAND_ALONE TYPE OUTPUT
    def perform_stand_alone_type_output(self):
        self._set_data_analysis_obj_from_arg_analysis_uid()
        if self.args.print_output_types_sample_set:
            self._stand_alone_type_output_data_set_sample()
            self._stand_alone_seq_output_from_type_output_data_set_sample()
        else:
            self._stand_alone_type_output_data_set()
            self._stand_alone_seq_output_from_type_output_data_set()
        if not self.args.no_figures:
            self.number_of_samples = len(self.output_seq_count_table_obj.sorted_sample_uid_list)
            self._plot_if_not_too_many_samples(self._plot_sequence_stacked_bar_from_seq_output_table)
            self._plot_if_not_too_many_samples(self._plot_type_stacked_bar_from_type_output_table)
        else:
            print('\nFigure plotting skipped at user\'s request')
        if not self.args.no_ordinations:
            self._do_data_analysis_ordinations()

    def _stand_alone_seq_output_from_type_output_data_set(self):
        self.output_seq_count_table_obj = output.SequenceCountTableCreator(
            call_type='analysis',
            num_proc=self.args.num_proc,
            symportal_root_dir=self.symportal_root_directory,
            ds_uids_output_str=self.args.print_output_types,
            output_dir=self.output_type_count_table_obj.output_dir,
            sorted_sample_uid_list=self.output_type_count_table_obj.sorted_list_of_vdss_uids_to_output,
            analysis_obj=self.data_analysis_object,
            time_date_str=self.output_type_count_table_obj.date_time_str)
        self.output_seq_count_table_obj.make_output_tables()

    def _stand_alone_seq_output_from_type_output_data_set_sample(self):
        self.output_seq_count_table_obj = output.SequenceCountTableCreator(
            call_type='analysis',
            num_proc=self.args.num_proc,
            symportal_root_dir=self.symportal_root_directory,
            dss_uids_output_str=self.args.print_output_types_sample_set,
            output_dir=self.output_type_count_table_obj.output_dir,
            sorted_sample_uid_list=self.output_type_count_table_obj.sorted_list_of_vdss_uids_to_output,
            analysis_obj=self.data_analysis_object,
            time_date_str=self.output_type_count_table_obj.date_time_str)
        self.output_seq_count_table_obj.make_output_tables()

    def _stand_alone_type_output_data_set(self):
        ds_uid_list = [int(ds_uid_str) for ds_uid_str in self.args.print_output_types.split(',')]
        self.output_type_count_table_obj = output.OutputTypeCountTable(
            num_proc=self.args.num_proc, within_clade_cutoff=self.within_clade_cutoff,
            call_type='stand_alone', symportal_root_directory=self.symportal_root_directory,
            data_set_uids_to_output=set(ds_uid_list), data_analysis_obj=self.data_analysis_object,
            date_time_str=self.data_analysis_object.time_stamp)
        self.output_type_count_table_obj.output_types()

    def _stand_alone_type_output_data_set_sample(self):
        dss_uid_list = [int(dss_uid_str) for dss_uid_str in self.args.print_output_types_sample_set.split(',')]
        self.output_type_count_table_obj = output.OutputTypeCountTable(
            num_proc=self.args.num_proc, within_clade_cutoff=self.within_clade_cutoff,
            call_type='stand_alone', symportal_root_directory=self.symportal_root_directory,
            data_set_sample_uid_set_to_output=set(dss_uid_list), data_analysis_obj=self.data_analysis_object,
            date_time_str=self.data_analysis_object.time_stamp)
        self.output_type_count_table_obj.output_types()

    # ITS2 TYPE PROFILE STAND_ALONE DISTANCES
    def perform_type_distance_stand_alone(self):
        """Generate the within clade pairwise distances between ITS2 type profiles either using a BrayCurtis- or Unifrac-based
        method. Also calculate the PCoA and plot as scatter plot for each."""
        self._verify_data_analysis_uid_provided()
        self._set_data_analysis_obj_from_arg_analysis_uid()
        self.run_type_distances_dependent_on_methods()
        self._plot_type_distances_from_distance_object()

    def run_type_distances_dependent_on_methods(self):
        """Start an instance of the correct distance class running."""
        if self.args.distance_method == 'unifrac':
            if self.args.between_type_distances_sample_set:
                self._start_type_unifrac_data_set_samples()
            else:
                self._start_type_unifrac_data_sets()
        elif self.args.distance_method == 'braycurtis':
            if self.args.between_type_distances_sample_set:
                self._start_type_braycurtis_data_set_samples()
            else:
                self._start_type_braycurtis_data_sets()

    def _start_type_braycurtis_data_sets(self):
        self.distance_object = distance.TypeBrayCurtisDistPCoACreator(
            call_type='stand_alone', data_analysis_obj=self.data_analysis_object,
            date_time_string=self.data_analysis_object.time_stamp,
            symportal_root_directory=self.symportal_root_directory,
            data_set_uid_list=[int(ds_uid_str) for ds_uid_str in self.args.between_type_distances.split(',')],
        )
        self.distance_object.compute_braycurtis_dists_and_pcoa_coords()

    def _start_type_braycurtis_data_set_samples(self):
        self.distance_object = distance.TypeBrayCurtisDistPCoACreator(
            call_type='stand_alone', data_analysis_obj=self.data_analysis_object,
            date_time_string=self.data_analysis_object.time_stamp,
            symportal_root_directory=self.symportal_root_directory,
            data_set_sample_uid_list=[int(ds_uid_str) for ds_uid_str in
                                      self.args.between_type_distances_sample_set.split(',')],
        )
        self.distance_object.compute_braycurtis_dists_and_pcoa_coords()

    def _start_type_unifrac_data_sets(self):
        self.distance_object = distance.TypeUnifracDistPCoACreator(
            call_type='stand_alone',
            data_analysis_obj=self.data_analysis_object,
            date_time_string=self.data_analysis_object.time_stamp,
            num_processors=self.args.num_proc, symportal_root_directory=self.symportal_root_directory,
            data_set_uid_list=[int(ds_uid_str) for ds_uid_str in
                               self.args.between_type_distances.split(',')]
        )
        self.distance_object.compute_unifrac_dists_and_pcoa_coords()

    def _start_type_unifrac_data_set_samples(self):
        self.distance_object = distance.TypeUnifracDistPCoACreator(
            call_type='stand_alone',
            data_analysis_obj=self.data_analysis_object,
            date_time_string=self.data_analysis_object.time_stamp,
            num_processors=self.args.num_proc, symportal_root_directory=self.symportal_root_directory,
            data_set_sample_uid_list=[int(ds_uid_str) for ds_uid_str in
                                      self.args.between_type_distances_sample_set.split(',')]
        )
        self.distance_object.compute_unifrac_dists_and_pcoa_coords()

    # SAMPLE STAND_ALONE DISTANCES
    def _perform_sample_distance_stand_alone(self):
        self._run_sample_distances_dependent_on_methods()
        self._plot_sample_distances_from_distance_object()

    def _run_sample_distances_dependent_on_methods(self):
        """Start an instance of the correct distance class running."""
        if self.args.distance_method == 'unifrac':
            if self.args.between_sample_distances_sample_set:
                self._start_sample_unifrac_data_set_samples()
            else:
                self._start_sample_unifrac_data_sets()
        elif self.args.distance_method == 'braycurtis':
            if self.args.between_sample_distances_sample_set:
                self._start_sample_braycurtis_data_set_samples()
            else:
                self._start_sample_braycurtis_data_sets()

    def _start_sample_unifrac_data_set_samples(self):
        dss_uid_list = [int(ds_uid_str) for ds_uid_str in self.args.between_sample_distances_sample_set.split(',')]
        self.distance_object = distance.SampleUnifracDistPCoACreator(
            call_type='stand_alone',
            data_set_sample_uid_list=dss_uid_list,
            num_processors=self.args.num_proc,
            symportal_root_directory=self.symportal_root_directory)
        self.distance_object.compute_unifrac_dists_and_pcoa_coords()

    def _start_sample_unifrac_data_sets(self):
        ds_uid_list = [int(ds_uid_str) for ds_uid_str in self.args.between_sample_distances.split(',')]
        self.distance_object = distance.SampleUnifracDistPCoACreator(
            call_type='stand_alone',
            data_set_uid_list=ds_uid_list,
            num_processors=self.args.num_proc,
            symportal_root_directory=self.symportal_root_directory)
        self.distance_object.compute_unifrac_dists_and_pcoa_coords()

    def _start_sample_braycurtis_data_set_samples(self):
        dss_uid_list = [int(ds_uid_str) for ds_uid_str in self.args.between_sample_distances_sample_set.split(',')]
        self.distance_object = distance.SampleBrayCurtisDistPCoACreator(
            symportal_root_directory=self.symportal_root_directory, date_time_string=self.date_time_str,
            data_set_sample_uid_list=dss_uid_list,
            call_type='stand_alone')

    def _start_sample_braycurtis_data_sets(self):
        ds_uid_list = [int(ds_uid_str) for ds_uid_str in self.args.between_sample_distances.split(',')]
        self.distance_object = distance.SampleBrayCurtisDistPCoACreator(
            symportal_root_directory=self.symportal_root_directory, date_time_string=self.date_time_str,
            data_set_uid_list=ds_uid_list,
            call_type='stand_alone')

    #VACUUM DB
    def perform_vacuum_database(self):
        print('Vacuuming database')
        self.vacuum_db()
        print('Vacuuming complete')

    @staticmethod
    def vacuum_db():
        from django.db import connection
        cursor = connection.cursor()
        cursor.execute("VACUUM")
        connection.close()

    # DISPLAY DB CONTENTS FUNCTIONS
    @staticmethod
    def perform_display_data_sets():
        data_set_id_to_obj_dict = {ds.id: ds for ds in list(DataSet.objects.all())}
        sorted_list_of_ids = sorted(list(data_set_id_to_obj_dict.keys()))
        for ds_id in sorted_list_of_ids:
            ds_in_q = data_set_id_to_obj_dict[ds_id]
            print(f'{ds_in_q.id}: {ds_in_q.name}\t{ds_in_q.time_stamp}')

    @staticmethod
    def perform_display_analysis_types():
        data_analysis_id_to_obj_dict = {da.id: da for da in list(DataAnalysis.objects.all())}
        sorted_list_of_ids = sorted(list(data_analysis_id_to_obj_dict.keys()))
        for da_id in sorted_list_of_ids:
            da_in_q = data_analysis_id_to_obj_dict[da_id]
            print(f'{da_in_q.id}: {da_in_q.name}\t{da_in_q.time_stamp}')

if __name__ == "__main__":
    spwfm = SymPortalWorkFlowManager()
    spwfm.start_work_flow()
