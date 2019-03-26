from dbApp.models import (DataSet, ReferenceSequence, DataSetSampleSequence, AnalysisType, DataSetSample,
                          DataAnalysis)
from multiprocessing import Queue, Process, Manager
import sys
from django import db
from datetime import datetime
import os
import json
from general import write_list_to_destination
from collections import defaultdict
import pandas as pd
import numpy as np
import sp_config
import virtual_objects
import time


class OutputTypeCountTable:
    """May need to have access to a list of all of the samples
    Let's see specifically which attributes of the samples we need

    Sample attributes we'll need (could have virtual sample):
    data set id
    sample id

    Things we need to calculate:


    Method:
    get a sorted list of the analysistypes for the output (either dataset or datasetsample defined)
    For each type calculate average relabund of each DIV, and SD
    Do class on a vat by vat basis
    """
    def __init__(
            self, num_proc, within_clade_cutoff, call_type, symportal_root_directory, data_set_uids_to_output=None, data_set_sample_uid_set_to_output=None,
            data_analysis_obj=None, data_analysis_uid=None, virtual_object_manager=None, date_time_str=None):

        self.data_set_uid_set_to_output, self.data_set_sample_uid_set_to_output = self._init_dss_and_ds_uids(
            data_set_sample_uid_set_to_output, data_set_uids_to_output)

        # Need to pass in the passed attributes rather than the self. attributes so we know which one is None
        self.virtual_object_manager = self._init_virtual_object_manager(
            virtual_object_manager, data_set_uids_to_output, data_set_sample_uid_set_to_output,
            num_proc, within_clade_cutoff)

        self.vcc_uids_to_output = self._set_vcc_uids_to_output()

        self.data_analysis_obj = self._init_da_object(data_analysis_obj, data_analysis_uid)

        if call_type == 'analysis':
            self.date_time_str = self.data_analysis_obj.time_stamp
        elif call_type == 'stand_alone':
            self.date_time_str = str(datetime.now()).replace(' ', '_').replace(':', '-')

        self.clades_of_output = set()
        # A sorting of the vats of the output only by the len of the vccs of the output that they associate with
        # i.e. before they are then sorted by clade. This will be used when calculating the order of the samples
        self.overall_sorted_list_of_vats = None
        # The above overall_sorted_list_of_vats is then ordered by clade to produce clade_sorted_list_of_vats_to_output
        self.clade_sorted_list_of_vats_to_output = self._set_clade_sorted_list_of_vats_to_output()
        self.sorted_list_of_vdss_uids_to_output = self._set_sorted_list_of_vdss_to_output()
        self.number_of_samples = None
        self.call_type = call_type
        self.pre_headers = None
        self.rel_abund_output_df, self.abs_abund_output_df = self._init_dfs()
        # set of all of the species found in the vats
        self.species_set = set()
        self.species_ref_dict = self._set_species_ref_dict()
        self.output_file_paths_list = []
        self.output_dir = os.path.join(symportal_root_directory, 'outputs', 'analyses', str(self.data_analysis_obj.id), self.date_time_str)
        os.makedirs(self.output_dir, exist_ok=True)
        self.path_to_relative_count_table = os.path.join(
            self.output_dir, f'{self.data_analysis_obj.id}_'
                             f'{self.data_analysis_obj.name}_'
                             f'{self.date_time_str}.profiles.relative.txt')
        self.path_to_absolute_count_table = os.path.join(
            self.output_dir, f'{self.data_analysis_obj.id}_'
                             f'{self.data_analysis_obj.name}_'
                             f'{self.date_time_str}.profiles.absolute.txt')
        self.output_file_paths_list.extend([
            self.path_to_relative_count_table, self.path_to_absolute_count_table])

    def _set_vcc_uids_to_output(self):
        list_of_sets_of_vcc_uids_in_vdss = [
            self.virtual_object_manager.vdss_manager.vdss_dict[vdss_uid].set_of_cc_uids for vdss_uid in
            self.data_set_sample_uid_set_to_output]
        vcc_uids_to_output = list_of_sets_of_vcc_uids_in_vdss[0].union(*list_of_sets_of_vcc_uids_in_vdss[1:])
        return vcc_uids_to_output

    def output_types(self):
        print('\n\nOutputting ITS2 type profile abundance count tables\n')
        self._populate_main_body_of_dfs()

        self._populate_sample_name_series()

        self._populate_meta_info_of_dfs()

        self._write_out_dfs()

    def _populate_sample_name_series(self):
        dss_name_ordered_list = [self.virtual_object_manager.vdss_manager.vdss_dict[vdss_uid].name for vdss_uid in self.sorted_list_of_vdss_uids_to_output]

        sample_name_series_data = []

        for _ in range(len(self.pre_headers)):
            sample_name_series_data.append(np.nan)

        for name in dss_name_ordered_list:
            sample_name_series_data.append(name)

        # add two nan for the remainder
        for _ in range(len(self.abs_abund_output_df.index.tolist()) - (len(self.pre_headers) + len(dss_name_ordered_list))):
            sample_name_series_data.append(np.nan)

        sample_name_series = pd.Series(
            name='sample_name',
            data=sample_name_series_data,
            index=self.abs_abund_output_df.index.tolist())

        self.abs_abund_output_df.insert(loc=0, column='sample_name', value=sample_name_series)
        self.rel_abund_output_df.insert(loc=0, column='sample_name', value=sample_name_series)

    def _populate_meta_info_of_dfs(self):
        self._append_species_header_to_dfs()
        self._append_species_references_to_dfs()
        if self.call_type == 'analysis':
            self._append_meta_info_for_analysis_call_type()
        else:
            # call_type=='stand_alone'
            self._append_meta_info_for_stand_alone_call_type()

    def _write_out_dfs(self):
        print('\n\nITS2 type profile count tables output to:')
        self.rel_abund_output_df.to_csv(self.path_to_relative_count_table, sep="\t", header=False)
        print(self.path_to_relative_count_table)
        self.abs_abund_output_df.to_csv(self.path_to_absolute_count_table, sep="\t", header=False)
        print(f'{self.path_to_absolute_count_table}\n\n')

    def _append_meta_info_for_stand_alone_call_type(self):
        data_sets_of_analysis = len(self.data_analysis_obj.list_of_data_set_uids.split(','))
        if self.call_type == 'stand_alone_data_sets':
            meta_info_string_items = self._make_stand_alone_data_set_meta_info_string(data_sets_of_analysis)
        else:
            meta_info_string_items = self._make_stand_alone_data_set_samples_meta_info_string(data_sets_of_analysis)
        self._append_meta_info_summary_string_to_dfs(meta_info_string_items)
        self._append_data_set_info_to_dfs()

    def _append_meta_info_for_analysis_call_type(self):
        meta_info_string_items = self._make_analysis_meta_info_string()
        self._append_meta_info_summary_string_to_dfs(meta_info_string_items)
        self._append_data_set_info_to_dfs()

    def _append_species_references_to_dfs(self):
        # now add the references for each of the associated species
        for species in self.species_set:
            if species in self.species_ref_dict.keys():
                temp_series = pd.Series([self.species_ref_dict[species]], index=[list(self.rel_abund_output_df)[0]],
                                        name=species)
                self.rel_abund_output_df = self.rel_abund_output_df.append(temp_series)
                self.abs_abund_output_df = self.abs_abund_output_df.append(temp_series)

    def _append_species_header_to_dfs(self):
        # add a blank row with just the header Species reference
        temp_blank_series_with_name = pd.Series(name='Species reference')
        self.rel_abund_output_df = self.rel_abund_output_df.append(temp_blank_series_with_name)
        self.abs_abund_output_df = self.abs_abund_output_df.append(temp_blank_series_with_name)

    def _append_data_set_info_to_dfs(self):
        for data_set_object in DataSet.objects.filter(id__in=self.data_set_uid_set_to_output):
            data_set_meta_list = [
                f'Data_set ID: {data_set_object.id}; '
                f'Data_set name: {data_set_object.name}; '
                f'submitting_user: {data_set_object.submitting_user}; '
                f'time_stamp: {data_set_object.time_stamp}']

            temp_series = pd.Series(
                data_set_meta_list, index=[list(self.rel_abund_output_df)[0]], name='data_set_info')
            self.rel_abund_output_df = self.rel_abund_output_df.append(temp_series)
            self.abs_abund_output_df = self.abs_abund_output_df.append(temp_series)

    def _make_analysis_meta_info_string(self):
        meta_info_string_items = [
            f'Output as part of data_analysis ID: {self.data_analysis_obj.id}; '
            f'Number of data_set objects as part of analysis = {len(self.data_set_uid_set_to_output)}; '
            f'submitting_user: {self.data_analysis_obj.submitting_user}; '
            f'time_stamp: {self.data_analysis_obj.time_stamp}']
        return meta_info_string_items

    def _append_meta_info_summary_string_to_dfs(self, meta_info_string_items):
        temp_series = pd.Series(
            meta_info_string_items, index=[list(self.rel_abund_output_df)[0]], name='meta_info_summary')
        self.rel_abund_output_df = self.rel_abund_output_df.append(temp_series)
        self.abs_abund_output_df = self.abs_abund_output_df.append(temp_series)

    def _make_stand_alone_data_set_meta_info_string(self, data_sets_of_analysis):
        meta_info_string_items = [
            f'Stand_alone_data_sets output by {sp_config.user_name} on {self.date_time_str}; '
            f'data_analysis ID: {self.data_analysis_obj.id}; '
            f'Number of data_set objects as part of output = {len(self.data_set_uid_set_to_output)}; '
            f'Number of data_set objects as part of analysis = {data_sets_of_analysis}']
        return meta_info_string_items

    def _make_stand_alone_data_set_samples_meta_info_string(self, data_sets_of_analysis):
        # self.call_type == 'stand_alone_data_set_samples'
        meta_info_string_items = [
            f'Stand_alone_data_set_samples output by {sp_config.user_name} on {self.date_time_str}; '
            f'data_analysis ID: {self.data_analysis_obj.id}; '
            f'Number of data_set objects as part of output = {len(self.data_set_uid_set_to_output)}; '
            f'Number of data_set objects as part of analysis = {data_sets_of_analysis}']
        return meta_info_string_items

    def _populate_main_body_of_dfs(self):
        print('\nPopulating output dfs:')
        for vat in self.clade_sorted_list_of_vats_to_output:
            sys.stdout.write(f'\r{vat.name}')
            tosp = self.TypeOutputSeriesPopulation(parent_output_type_count_table=self, vat=vat)
            data_relative_list, data_absolute_list = tosp.make_output_series()
            self.rel_abund_output_df[vat.id] = data_relative_list
            self.abs_abund_output_df[vat.id] = data_absolute_list
            if vat.species != 'None':
                self.species_set.update(vat.species.split(','))

    class TypeOutputSeriesPopulation:
        """will create a relative abundance and absolute abundance
        output pandas series for a given VirtualAnalysisType
        """
        def __init__(self, parent_output_type_count_table, vat):
            self.output_type_count_table = parent_output_type_count_table
            self.vat = vat
            self.data_relative_list = []
            self.data_absolute_list = []

        def make_output_series(self):
            """type_uid"""
            self._pop_type_uid()

            self._pop_type_clade()

            self._pop_maj_seq_str()

            self._pop_species()

            self._pop_type_local_and_global_abundances()

            self._pop_type_name()

            self._pop_type_abundances()

            self._pop_vat_accession_name()

            self._pop_av_and_stdev_abund()

            return self.data_relative_list, self.data_absolute_list


        def _pop_av_and_stdev_abund(self):
            average_abund_and_sd_string = ''
            for rs_id in list(self.vat.multi_modal_detection_rel_abund_df):
                if average_abund_and_sd_string == '':
                    average_abund_and_sd_string = self._append_rel_abund_and_sd_str_for_rs(
                        average_abund_and_sd_string, rs_id)
                else:
                    average_abund_and_sd_string = self._append_dash_or_slash_if_maj_seq(average_abund_and_sd_string,
                                                                                        rs_id)
                    average_abund_and_sd_string = self._append_rel_abund_and_sd_str_for_rs(
                        average_abund_and_sd_string, rs_id)
            self.data_relative_list.append(average_abund_and_sd_string)
            self.data_absolute_list.append(average_abund_and_sd_string)

        def _append_dash_or_slash_if_maj_seq(self, average_abund_and_sd_string, rs_id):
            if rs_id in self.vat.majority_reference_sequence_uid_set:
                average_abund_and_sd_string += '/'
            else:
                average_abund_and_sd_string += '-'
            return average_abund_and_sd_string

        def _append_rel_abund_and_sd_str_for_rs(self, average_abund_and_sd_string, rs_id):
            average_abund_str = "{0:.3f}".format(self.vat.multi_modal_detection_rel_abund_df[rs_id].mean())
            std_dev_str = "{0:.3f}".format(self.vat.multi_modal_detection_rel_abund_df[rs_id].std())
            average_abund_and_sd_string += f'{average_abund_str}[{std_dev_str}]'
            return average_abund_and_sd_string

        def _pop_vat_accession_name(self):
            vat_accession_name = self.vat.generate_name(
                at_df=self.vat.multi_modal_detection_rel_abund_df,
                use_rs_ids_rather_than_names=True)
            self.data_relative_list.append(vat_accession_name)
            self.data_absolute_list.append(vat_accession_name)

        def _pop_type_abundances(self):
            # type abundances
            temp_rel_abund_holder_list = []
            temp_abs_abund_holder_list = []
            for vdss_uid in self.output_type_count_table.sorted_list_of_vdss_uids_to_output:
                count = 0
                vdss_obj = self.output_type_count_table.virtual_object_manager.vdss_manager.vdss_dict[vdss_uid]
                for vcc_uid in vdss_obj.set_of_cc_uids:
                    if vcc_uid in self.vat.type_output_rel_abund_series:
                        count += 1
                        temp_rel_abund_holder_list.append(self.vat.type_output_rel_abund_series[vcc_uid])
                        temp_abs_abund_holder_list.append(self.vat.type_output_abs_abund_series[vcc_uid])

                if count == 0:  # type not found in vdss
                    temp_rel_abund_holder_list.append(0)
                    temp_abs_abund_holder_list.append(0)
                if count > 1:  # more than one vcc from vdss associated with type
                    raise RuntimeError('More than one vcc of vdss matched vat in output')
            self.data_relative_list.extend(temp_rel_abund_holder_list)
            self.data_absolute_list.extend(temp_abs_abund_holder_list)

        def _pop_type_name(self):
            # name
            self.data_absolute_list.append(self.vat.name)
            self.data_relative_list.append(self.vat.name)

        def _pop_type_local_and_global_abundances(self):
            # local_output_type_abundance
            # all analysis_type_abundance
            vccs_of_type = self.vat.clade_collection_obj_set_profile_assignment
            vccs_of_type_from_output = [vcc for vcc in vccs_of_type if
                                        vcc.vdss_uid in self.output_type_count_table.sorted_list_of_vdss_uids_to_output]
            self.data_absolute_list.extend([str(len(vccs_of_type_from_output)), str(len(vccs_of_type))])
            self.data_relative_list.extend([str(len(vccs_of_type_from_output)), str(len(vccs_of_type))])

        def _pop_species(self):
            # species
            self.data_absolute_list.append(self.vat.species)
            self.data_relative_list.append(self.vat.species)

        def _pop_maj_seq_str(self):
            # majority sequences string e.g. C3/C3b
            ordered_maj_seq_names = []
            for rs_id in list(self.vat.multi_modal_detection_rel_abund_df):
                for rs in self.vat.footprint_as_ref_seq_objs_set:
                    if rs.id == rs_id and rs in self.vat.majority_reference_sequence_obj_set:
                        ordered_maj_seq_names.append(rs.name)
            maj_seq_str = '/'.join(ordered_maj_seq_names)
            self.data_absolute_list.append(maj_seq_str)
            self.data_relative_list.append(maj_seq_str)

        def _pop_type_clade(self):
            # clade
            self.data_absolute_list.append(self.vat.clade)
            self.data_relative_list.append(self.vat.clade)

        def _pop_type_uid(self):
            # Type uid
            self.data_absolute_list.append(self.vat.id)
            self.data_relative_list.append(self.vat.id)


    def _init_da_object(self, data_analysis_obj, data_analysis_uid):
        if data_analysis_uid:
            self.data_analysis_obj = DataAnalysis.objects.get(id=data_analysis_uid)
        else:
            self.data_analysis_obj = data_analysis_obj
        return self.data_analysis_obj

    def _init_dfs(self):
        self.pre_headers = ['ITS2 type profile UID', 'Clade', 'Majority ITS2 sequence',
                       'Associated species', 'ITS2 type abundance local', 'ITS2 type abundance DB', 'ITS2 type profile']
        post_headers = ['Sequence accession / SymPortal UID', 'Average defining sequence proportions and [stdev]']
        self.df_index = self.pre_headers + self.sorted_list_of_vdss_uids_to_output + post_headers
        return pd.DataFrame(index=self.df_index), pd.DataFrame(index=self.df_index)

    def _init_dss_and_ds_uids(self, data_set_sample_uid_set_to_output, data_set_uids_to_output):
        if data_set_sample_uid_set_to_output:
            self.data_set_sample_uid_set_to_output = data_set_sample_uid_set_to_output
            self.data_set_uid_set_to_output = [ds.id for ds in DataSet.objects.filter(
                datasetsample__in=self.data_set_sample_uid_set_to_output).distinct()]
        else:
            self.data_set_uid_set_to_output = data_set_uids_to_output
            self.data_set_sample_uid_set_to_output = [
                dss.id for dss in DataSetSample.objects.filter(
                    data_submission_from__in=self.data_set_uid_set_to_output)]
        return self.data_set_uid_set_to_output, self.data_set_sample_uid_set_to_output

    def _init_virtual_object_manager(
            self, virtual_object_manager, data_set_uids_to_output, data_set_sample_uid_set_to_output,
            num_proc, within_clade_cutoff):
        if virtual_object_manager:
            return virtual_object_manager
        else:
            if data_set_uids_to_output:
                self.virtual_object_manager = virtual_objects.VirtualObjectManager(
                    num_proc=num_proc, within_clade_cutoff=within_clade_cutoff,
                    list_of_data_set_uids=data_set_uids_to_output)
            else:
                self.virtual_object_manager = virtual_objects.VirtualObjectManager(
                    num_proc=num_proc, within_clade_cutoff=within_clade_cutoff,
                    list_of_data_set_sample_uids=data_set_sample_uid_set_to_output)

            print('\nInstantiating VirtualAnalysisTypes')
            for at in AnalysisType.objects.filter(
                    cladecollectiontype__clade_collection_found_in__data_set_sample_from__in=
                    self.data_set_sample_uid_set_to_output).distinct():
                sys.stdout.write(f'\r{at.name}')
                self.virtual_object_manager.vat_manager.make_vat_post_profile_assignment_from_analysis_type(at)
        return self.virtual_object_manager

    def _get_data_set_uids_of_data_sets(self):
        vds_uid_set = set()
        for vdss in [vdss for vdss in self.virtual_object_manager.vdss_manager.vdss_dict.values() if
                     vdss.uid in self.data_set_sample_uid_set_to_output]:
            vds_uid_set.add(vdss.data_set_id)
        return vds_uid_set

    def _set_sorted_list_of_vdss_to_output(self):
        """Generate the list of dss uids that will be the order that we will use for the index
        of the output dataframes. The order should be the samples of the most abundant VirtualAnalsysiTypes first
        and within this order sorted by the relative abundance of the VirtualAnalsysiTypes within the sample"""
        sorted_vdss_uid_list = []
        vcc_dict = self.virtual_object_manager.vcc_manager.vcc_dict
        for vat in self.overall_sorted_list_of_vats:
            for vcc_uid in vat.type_output_rel_abund_series.sort_values(ascending=False).index.tolist():
                vdss_uid_of_vcc = vcc_dict[vcc_uid].vdss_uid
                # Because several vccs can come from the same vdss we need to make sure that the vdss has
                # not already been put into the sorted_vdss_uid_list
                if vdss_uid_of_vcc in self.data_set_sample_uid_set_to_output and vdss_uid_of_vcc not in sorted_vdss_uid_list:
                    sorted_vdss_uid_list.append(vdss_uid_of_vcc)

        # add the samples that didn't have a type associated to them
        sorted_vdss_uid_list.extend(
            [dss_uid for dss_uid in
             self.data_set_sample_uid_set_to_output if dss_uid not in sorted_vdss_uid_list])

        return sorted_vdss_uid_list

    def _set_clade_sorted_list_of_vats_to_output(self):
        """Get list of analysis type sorted by clade, and then by
        len of the cladecollections associated to them from the output
        """
        list_of_tup_vat_to_vccs_of_output = []
        for vat in self.virtual_object_manager.vat_manager.vat_dict.values():
            self.clades_of_output.add(vat.clade)
            vccs_of_output_of_vat = []
            for vcc in vat.clade_collection_obj_set_profile_assignment:
                if vcc.vdss_uid in self.data_set_sample_uid_set_to_output:
                    vccs_of_output_of_vat.append(vcc)
            list_of_tup_vat_to_vccs_of_output.append((vat, len(vccs_of_output_of_vat)))

        self.overall_sorted_list_of_vats = [vat for vat, num_vcc_of_output in
                              sorted(list_of_tup_vat_to_vccs_of_output, key=lambda x: x[1], reverse=True) if num_vcc_of_output != 0]

        clade_ordered_type_order = []
        for clade in list('ABCDEFGHI'):
            clade_ordered_type_order.extend([vat for vat in self.overall_sorted_list_of_vats if vat.clade == clade])
        return clade_ordered_type_order

    def _set_species_ref_dict(self):
        return {
        'S. microadriaticum': 'Freudenthal, H. D. (1962). Symbiodinium gen. nov. and Symbiodinium microadriaticum '
                              'sp. nov., a Zooxanthella: Taxonomy, Life Cycle, and Morphology. The Journal of '
                              'Protozoology 9(1): 45-52',
        'S. pilosum': 'Trench, R. (2000). Validation of some currently used invalid names of dinoflagellates. '
                      'Journal of Phycology 36(5): 972-972.\tTrench, R. K. and R. J. Blank (1987). '
                      'Symbiodinium microadriaticum Freudenthal, S. goreauii sp. nov., S. kawagutii sp. nov. and '
                      'S. pilosum sp. nov.: Gymnodinioid dinoflagellate symbionts of marine invertebrates. '
                      'Journal of Phycology 23(3): 469-481.',
        'S. natans': 'Hansen, G. and N. Daugbjerg (2009). Symbiodinium natans sp. nob.: A free-living '
                     'dinoflagellate from Tenerife (northeast Atlantic Ocean). Journal of Phycology 45(1): 251-263.',
        'S. tridacnidorum': 'Lee, S. Y., H. J. Jeong, N. S. Kang, T. Y. Jang, S. H. Jang and T. C. Lajeunesse (2015). '
                            'Symbiodinium tridacnidorum sp. nov., a dinoflagellate common to Indo-Pacific giant clams,'
                            ' and a revised morphological description of Symbiodinium microadriaticum Freudenthal, '
                            'emended Trench & Blank. European Journal of Phycology 50(2): 155-172.',
        'S. linucheae': 'Trench, R. K. and L.-v. Thinh (1995). Gymnodinium linucheae sp. nov.: The dinoflagellate '
                        'symbiont of the jellyfish Linuche unguiculata. European Journal of Phycology 30(2): 149-154.',
        'S. minutum': 'Lajeunesse, T. C., J. E. Parkinson and J. D. Reimer (2012). A genetics-based description of '
                      'Symbiodinium minutum sp. nov. and S. psygmophilum sp. nov. (dinophyceae), two dinoflagellates '
                      'symbiotic with cnidaria. Journal of Phycology 48(6): 1380-1391.',
        'S. antillogorgium': 'Parkinson, J. E., M. A. Coffroth and T. C. LaJeunesse (2015). "New species of Clade B '
                             'Symbiodinium (Dinophyceae) from the greater Caribbean belong to different functional '
                             'guilds: S. aenigmaticum sp. nov., S. antillogorgium sp. nov., S. endomadracis sp. nov., '
                             'and S. pseudominutum sp. nov." Journal of phycology 51(5): 850-858.',
        'S. pseudominutum': 'Parkinson, J. E., M. A. Coffroth and T. C. LaJeunesse (2015). "New species of Clade B '
                            'Symbiodinium (Dinophyceae) from the greater Caribbean belong to different functional '
                            'guilds: S. aenigmaticum sp. nov., S. antillogorgium sp. nov., S. endomadracis sp. nov., '
                            'and S. pseudominutum sp. nov." Journal of phycology 51(5): 850-858.',
        'S. psygmophilum': 'Lajeunesse, T. C., J. E. Parkinson and J. D. Reimer (2012). '
                           'A genetics-based description of '
                           'Symbiodinium minutum sp. nov. and S. psygmophilum sp. nov. (dinophyceae), '
                           'two dinoflagellates '
                           'symbiotic with cnidaria. Journal of Phycology 48(6): 1380-1391.',
        'S. muscatinei': 'No reference available',
        'S. endomadracis': 'Parkinson, J. E., M. A. Coffroth and T. C. LaJeunesse (2015). "New species of Clade B '
                           'Symbiodinium (Dinophyceae) from the greater Caribbean belong to different functional '
                           'guilds: S. aenigmaticum sp. nov., S. antillogorgium sp. nov., S. endomadracis sp. nov., '
                           'and S. pseudominutum sp. nov." Journal of phycology 51(5): 850-858.',
        'S. aenigmaticum': 'Parkinson, J. E., M. A. Coffroth and T. C. LaJeunesse (2015). "New species of Clade B '
                           'Symbiodinium (Dinophyceae) from the greater Caribbean belong to different functional '
                           'guilds: S. aenigmaticum sp. nov., S. antillogorgium sp. nov., S. endomadracis sp. nov., '
                           'and S. pseudominutum sp. nov." Journal of phycology 51(5): 850-858.',
        'S. goreaui': 'Trench, R. (2000). Validation of some currently used invalid names of dinoflagellates. '
                      'Journal of Phycology 36(5): 972-972.\tTrench, R. K. and R. J. Blank (1987). '
                      'Symbiodinium microadriaticum Freudenthal, S. goreauii sp. nov., S. kawagutii sp. nov. and '
                      'S. pilosum sp. nov.: Gymnodinioid dinoflagellate symbionts of marine invertebrates. '
                      'Journal of Phycology 23(3): 469-481.',
        'S. thermophilum': 'Hume, B. C. C., C. D`Angelo, E. G. Smith, J. R. Stevens, J. Burt and J. Wiedenmann (2015).'
                           ' Symbiodinium thermophilum sp. nov., a thermotolerant symbiotic alga prevalent in corals '
                           'of the world`s hottest sea, the Persian/Arabian Gulf. Sci. Rep. 5.',
        'S. glynnii': 'LaJeunesse, T. C., D. T. Pettay, E. M. Sampayo, N. Phongsuwan, B. Brown, D. O. Obura, O. '
                      'Hoegh-Guldberg and W. K. Fitt (2010). Long-standing environmental conditions, geographic '
                      'isolation and host-symbiont specificity influence the relative ecological dominance and '
                      'genetic diversification of coral endosymbionts in the genus Symbiodinium. Journal of '
                      'Biogeography 37(5): 785-800.',
        'S. trenchii': 'LaJeunesse, T. C., D. C. Wham, D. T. Pettay, J. E. Parkinson, S. Keshavmurthy and C. A. Chen '
                       '(2014). Ecologically differentiated stress-tolerant endosymbionts in the dinoflagellate genus'
                       ' Symbiodinium (Dinophyceae) Clade D are different species. Phycologia 53(4): 305-319.',
        'S. eurythalpos': 'LaJeunesse, T. C., D. C. Wham, D. T. Pettay, J. E. Parkinson, '
                          'S. Keshavmurthy and C. A. Chen '
                          '(2014). Ecologically differentiated stress-tolerant '
                          'endosymbionts in the dinoflagellate genus'
                          ' Symbiodinium (Dinophyceae) Clade D are different species. Phycologia 53(4): 305-319.',
        'S. boreum': 'LaJeunesse, T. C., D. C. Wham, D. T. Pettay, J. E. Parkinson, S. Keshavmurthy and C. A. Chen '
                     '(2014). "Ecologically differentiated stress-tolerant endosymbionts in the dinoflagellate genus'
                     ' Symbiodinium (Dinophyceae) Clade D are different species." Phycologia 53(4): 305-319.',
        'S. voratum': 'Jeong, H. J., S. Y. Lee, N. S. Kang, Y. D. Yoo, A. S. Lim, M. J. Lee, H. S. Kim, W. Yih, H. '
                      'Yamashita and T. C. LaJeunesse (2014). Genetics and Morphology Characterize the Dinoflagellate'
                      ' Symbiodinium voratum, n. sp., (Dinophyceae) as the Sole Representative of Symbiodinium Clade E'
                      '. Journal of Eukaryotic Microbiology 61(1): 75-94.',
        'S. kawagutii': 'Trench, R. (2000). Validation of some currently used invalid names of dinoflagellates. '
                        'Journal of Phycology 36(5): 972-972.\tTrench, R. K. and R. J. Blank (1987). '
                        'Symbiodinium microadriaticum Freudenthal, S. goreauii sp. nov., S. kawagutii sp. nov. and '
                        'S. pilosum sp. nov.: Gymnodinioid dinoflagellate symbionts of marine invertebrates. '
                        'Journal of Phycology 23(3): 469-481.'
    }


class SequenceCountTableCreator:
    """ This is essentially broken into two parts. The first part goes through all of the DataSetSamples from
    the DataSets of the output and collects abundance information. The second part then puts this abundance
    information into a dataframe for both the absoulte and the relative abundance.
    This seq output can be run in two ways:
    1 - by providing DataSet uid lists
    2 - by providing DataSetSample uid lists
    Either way, after initial init, we will work on a sample by sample basis.
    """
    def __init__(
            self, symportal_root_dir, call_type, num_proc, dss_uids_output_str=None, ds_uids_output_str=None, output_dir=None,
            sorted_sample_uid_list=None, analysis_obj=None, time_date_str=None):
        self._init_core_vars(
            symportal_root_dir, analysis_obj, call_type, dss_uids_output_str, ds_uids_output_str, num_proc,
            output_dir, sorted_sample_uid_list, time_date_str)
        self._init_seq_abundance_collection_objects()
        self._init_vars_for_putting_together_the_dfs()
        self._init_output_paths()

    def _init_core_vars(self, symportal_root_dir, analysis_obj, call_type, dss_uids_output_str, ds_uids_output_str, num_proc,
                        output_dir, sorted_sample_uid_list, time_date_str):
        self._check_either_dss_or_dsss_uids_provided(dss_uids_output_str, ds_uids_output_str)
        if dss_uids_output_str:
            self.list_of_dss_objects = DataSetSample.objects.filter(id__in=[int(a) for a in dss_uids_output_str.split(',')])
            self.ds_objs_to_output = DataSet.objects.filter(datasetsample__in=self.list_of_dss_objects).distinct()
        elif ds_uids_output_str:
            uids_of_data_sets_to_output = [int(a) for a in ds_uids_output_str.split(',')]
            self.ds_objs_to_output = DataSet.objects.filter(id__in=uids_of_data_sets_to_output)
            self.list_of_dss_objects = DataSetSample.objects.filter(data_submission_from__in=self.ds_objs_to_output)

        self.ref_seqs_in_datasets = ReferenceSequence.objects.filter(
            datasetsamplesequence__data_set_sample_from__in=
            self.list_of_dss_objects).distinct()

        self.num_proc = num_proc
        if time_date_str:
            self.time_date_str = time_date_str
        else:
            self.time_date_str = str(datetime.now()).replace(' ', '_').replace(':', '-')
        self._set_output_dir(call_type, ds_uids_output_str, output_dir, symportal_root_dir)
        self.sorted_sample_uid_list = sorted_sample_uid_list
        self.analysis_obj = analysis_obj
        self.call_type = call_type
        self.output_user = sp_config.user_name
        self.clade_list = list('ABCDEFGHI')
        set_of_clades_found = {ref_seq.clade for ref_seq in self.ref_seqs_in_datasets}
        self.ordered_list_of_clades_found = [clade for clade in self.clade_list if clade in set_of_clades_found]


    @staticmethod
    def _check_either_dss_or_dsss_uids_provided(data_set_sample_ids_to_output_string, data_set_uids_to_output_as_comma_sep_string):
        if data_set_sample_ids_to_output_string is not None and data_set_uids_to_output_as_comma_sep_string is not None:
            raise RuntimeError('Provide either dss uids or ds uids for outputing sequence count tables')

    def _set_output_dir(self, call_type, data_set_uids_to_output_as_comma_sep_string, output_dir, symportal_root_dir):
        if call_type == 'submission':
            self.output_dir = os.path.abspath(os.path.join(
                symportal_root_dir, 'outputs', 'loaded_data_sets', data_set_uids_to_output_as_comma_sep_string))
        elif call_type == 'stand_alone':
            self.output_dir = os.path.abspath(os.path.join(symportal_root_dir, 'outputs', 'non_analysis', self.time_date_str))
        else:  # call_type == 'analysis
            self.output_dir = output_dir
        os.makedirs(self.output_dir, exist_ok=True)

    def _init_seq_abundance_collection_objects(self):
        """Output objects from first worker to be used by second worker"""
        self.dss_id_to_list_of_dsss_objects_dict_mp_dict = None
        self.dss_id_to_list_of_abs_and_rel_abund_of_contained_dsss_dicts_mp_dict = None
        self.dss_id_to_list_of_abs_and_rel_abund_clade_summaries_of_noname_seqs_mp_dict = None
        # this is the list that we will use the self.annotated_dss_name_to_cummulative_rel_abund_mp_dict to create
        # it is a list of the ref_seqs_ordered first by clade then by abundance.
        self.clade_abundance_ordered_ref_seq_list = []

    def _init_vars_for_putting_together_the_dfs(self):
        # variables concerned with putting together the dataframes
        self.dss_id_to_pandas_series_results_list_dict = None
        self.output_df_absolute = None
        self.output_df_relative = None
        self.output_seqs_fasta_as_list = []

    def _init_output_paths(self):
        self.output_paths_list = []
        if self.analysis_obj:

            self.path_to_seq_output_df_absolute = os.path.join(
                self.output_dir,
                f'{self.analysis_obj.id}_{self.analysis_obj.name}_{self.time_date_str}.seqs.absolute.txt')
            self.path_to_seq_output_df_relative = os.path.join(
                self.output_dir,
                f'{self.analysis_obj.id}_{self.analysis_obj.name}_{self.time_date_str}.seqs.relative.txt')

            self.output_fasta_path = os.path.join(
                self.output_dir, f'{self.analysis_obj.id}_{self.analysis_obj.name}_{self.time_date_str}.seqs.fasta')

        else:
            self.path_to_seq_output_df_absolute = os.path.join(self.output_dir,
                                                               f'{self.time_date_str}.seqs.absolute.txt')
            self.path_to_seq_output_df_relative = os.path.join(self.output_dir,
                                                               f'{self.time_date_str}.seqs.relative.txt')
            self.output_fasta_path = os.path.join(self.output_dir, f'{self.time_date_str}.seqs.fasta')

    def make_output_tables(self):
        print('\n\nOutputting sequence abundance count tables\n')
        self._collect_abundances_for_creating_the_output()

        self._generate_sample_output_series()

        self._create_ordered_output_dfs_from_series()

        self._add_uids_for_seqs_to_dfs()

        self._append_meta_info_to_df()

        self._write_out_dfs_and_fasta()

    def _write_out_dfs_and_fasta(self):
        self.output_df_absolute.to_csv(self.path_to_seq_output_df_absolute, sep="\t")
        self.output_paths_list.append(self.path_to_seq_output_df_absolute)
        self.output_df_relative.to_csv(self.path_to_seq_output_df_relative, sep="\t")
        self.output_paths_list.append(self.path_to_seq_output_df_relative)
        # we created the fasta above.
        write_list_to_destination(self.output_fasta_path, self.output_seqs_fasta_as_list)
        self.output_paths_list.append(self.output_fasta_path)
        print('\n\nITS2 sequence output files:')
        for path_item in self.output_paths_list:
            print(path_item)

    def _append_meta_info_to_df(self):
        # Now append the meta infromation for each of the data_sets that make up the output contents
        # this is information like the submitting user, what the uids of the datasets are etc.
        # There are several ways that this can be called.
        # it can be called as part of the submission: call_type = submission
        # part of an analysis output: call_type = analysis
        # or stand alone: call_type = 'stand_alone'
        # we should have an output for each scenario
        if self.call_type == 'submission':
            self._append_meta_info_to_df_submission()
        elif self.call_type == 'analysis':
            self._append_meta_info_to_df_analysis()
        else:
            # call_type=='stand_alone'
            self._append_meta_info_to_df_stand_alone()

    def _append_meta_info_to_df_submission(self):
        data_set_object = self.ds_objs_to_output[0]
        # there will only be one data_set object
        meta_info_string_items = [
            f'Output as part of data_set submission ID: {data_set_object.id}; '
            f'submitting_user: {data_set_object.submitting_user}; '
            f'time_stamp: {data_set_object.time_stamp}']
        temp_series = pd.Series(meta_info_string_items, index=[list(self.output_df_absolute)[0]],
                                name='meta_info_summary')
        self.output_df_absolute = self.output_df_absolute.append(temp_series)
        self.output_df_relative = self.output_df_relative.append(temp_series)

    def _append_meta_info_to_df_analysis(self):

        num_data_set_objects_as_part_of_analysis = len(self.analysis_obj.list_of_data_set_uids.split(','))
        meta_info_string_items = [
            f'Output as part of data_analysis ID: {self.analysis_obj.id}; '
            f'Number of data_set objects as part of analysis = {num_data_set_objects_as_part_of_analysis}; '
            f'submitting_user: {self.analysis_obj.submitting_user}; time_stamp: {self.analysis_obj.time_stamp}']
        temp_series = pd.Series(meta_info_string_items, index=[list(self.output_df_absolute)[0]],
                                name='meta_info_summary')
        self.output_df_absolute = self.output_df_absolute.append(temp_series)
        self.output_df_relative = self.output_df_relative.append(temp_series)
        for data_set_object in self.ds_objs_to_output:
            data_set_meta_list = [
                f'Data_set ID: {data_set_object.id}; '
                f'Data_set name: {data_set_object.name}; '
                f'submitting_user: {data_set_object.submitting_user}; '
                f'time_stamp: {data_set_object.time_stamp}']

            temp_series = pd.Series(data_set_meta_list, index=[list(self.output_df_absolute)[0]], name='data_set_info')
            self.output_df_absolute = self.output_df_absolute.append(temp_series)
            self.output_df_relative = self.output_df_relative.append(temp_series)

    def _append_meta_info_to_df_stand_alone(self):
        meta_info_string_items = [
            f'Stand_alone output by {self.output_user} on {self.time_date_str}; '
            f'Number of data_set objects as part of output = {len(self.ds_objs_to_output)}']
        temp_series = pd.Series(meta_info_string_items, index=[list(self.output_df_absolute)[0]],
                                name='meta_info_summary')
        self.output_df_absolute = self.output_df_absolute.append(temp_series)
        self.output_df_relative = self.output_df_relative.append(temp_series)
        for data_set_object in self.ds_objs_to_output:
            data_set_meta_list = [
                f'Data_set ID: {data_set_object.id}; '
                f'Data_set name: {data_set_object.name}; '
                f'submitting_user: {data_set_object.submitting_user}; '
                f'time_stamp: {data_set_object.time_stamp}']
            temp_series = pd.Series(data_set_meta_list, index=[list(self.output_df_absolute)[0]], name='data_set_info')
            self.output_df_absolute = self.output_df_absolute.append(temp_series)
            self.output_df_relative = self.output_df_relative.append(temp_series)

    def _add_uids_for_seqs_to_dfs(self):
        """Now add the UID for each of the sequences"""
        sys.stdout.write('\nGenerating accession and fasta\n')
        reference_sequences_in_data_sets_no_name = ReferenceSequence.objects.filter(
            datasetsamplesequence__data_set_sample_from__in=self.list_of_dss_objects,
            has_name=False).distinct()
        reference_sequences_in_data_sets_has_name = ReferenceSequence.objects.filter(
            datasetsamplesequence__data_set_sample_from__in=self.list_of_dss_objects,
            has_name=True).distinct()
        no_name_dict = {rs.id: rs.sequence for rs in reference_sequences_in_data_sets_no_name}
        has_name_dict = {rs.name: (rs.id, rs.sequence) for rs in reference_sequences_in_data_sets_has_name}
        accession_list = []
        num_cols = len(list(self.output_df_relative))
        for i, col_name in enumerate(list(self.output_df_relative)):
            sys.stdout.write('\rAppending accession info and creating fasta {}: {}/{}'.format(col_name, i, num_cols))
            if col_name in self.clade_abundance_ordered_ref_seq_list:
                if col_name[-2] == '_':
                    col_name_id = int(col_name[:-2])
                    accession_list.append(str(col_name_id))
                    self.output_seqs_fasta_as_list.append('>{}'.format(col_name))
                    self.output_seqs_fasta_as_list.append(no_name_dict[col_name_id])
                else:
                    col_name_tup = has_name_dict[col_name]
                    accession_list.append(str(col_name_tup[0]))
                    self.output_seqs_fasta_as_list.append('>{}'.format(col_name))
                    self.output_seqs_fasta_as_list.append(col_name_tup[1])
            else:
                accession_list.append(np.nan)
        temp_series = pd.Series(accession_list, name='seq_accession', index=list(self.output_df_relative))
        self.output_df_absolute = self.output_df_absolute.append(temp_series)
        self.output_df_relative = self.output_df_relative.append(temp_series)

    def _create_ordered_output_dfs_from_series(self):
        """Put together the pandas series that hold sequences abundance outputs for each sample in order of the samples
        either according to a predefined ordered list or by an order that will be generated below."""
        if self.sorted_sample_uid_list:
            sys.stdout.write('\nValidating sorted sample list and ordering dataframe accordingly\n')
            self._check_sorted_sample_list_is_valid()

            self._create_ordered_output_dfs_from_series_with_sorted_sample_list()

        else:
            sys.stdout.write('\nGenerating ordered sample list and ordering dataframe accordingly\n')
            self.sorted_sample_uid_list = self._generate_ordered_sample_list()

            self._create_ordered_output_dfs_from_series_with_sorted_sample_list()

    def _generate_ordered_sample_list(self):
        """ Returns a list which is simply the ids of the samples ordered
        This will order the samples according to which sequence is their most abundant.
        I.e. samples found to have the sequence which is most abundant in the largest number of sequences
        will be first. Within each maj sequence, the samples will be sorted by the abundance of that sequence
        in the sample.
        At the moment we are also ordering by clade just so that you see samples with the A's at the top
        of the output so that we minimise the number of 0's in the top left of the output
        honestly I think we could perhaps get rid of this and just use the over all abundance of the sequences
        discounting clade. This is what we do for the clade order when plotting.
        """
        output_df_relative = self._make_raw_relative_abund_df_from_series()
        ordered_sample_list = self._get_sample_order_from_rel_seq_abund_df(output_df_relative)
        return ordered_sample_list

    def _make_raw_relative_abund_df_from_series(self):
        output_df_relative = pd.concat(
            [list_of_series[1] for list_of_series in self.dss_id_to_pandas_series_results_list_dict.values()],
            axis=1)
        output_df_relative = output_df_relative.T
        # now remove the rest of the non abundance columns
        non_seq_columns = [
            'sample_name', 'raw_contigs', 'post_taxa_id_absolute_non_symbiodinium_seqs', 'post_qc_absolute_seqs',
            'post_qc_unique_seqs', 'post_taxa_id_unique_non_symbiodinium_seqs',
            'post_taxa_id_absolute_symbiodinium_seqs',
            'post_taxa_id_unique_symbiodinium_seqs', 'post_med_absolute', 'post_med_unique',
            'size_screening_violation_absolute', 'size_screening_violation_unique']
        no_name_seq_columns = ['noName Clade {}'.format(clade) for clade in list('ABCDEFGHI')]
        cols_to_drop = non_seq_columns + no_name_seq_columns
        output_df_relative.drop(columns=cols_to_drop, inplace=True)
        return output_df_relative

    def _get_sample_order_from_rel_seq_abund_df(self, sequence_only_df_relative):

        max_seq_ddict, no_maj_samps, seq_to_samp_ddict = self._generate_most_abundant_sequence_dictionaries(
            sequence_only_df_relative)

        return self._generate_ordered_sample_list_from_most_abund_seq_dicts(max_seq_ddict, no_maj_samps,
                                                                            seq_to_samp_ddict)

    @staticmethod
    def _generate_ordered_sample_list_from_most_abund_seq_dicts(max_seq_ddict, no_maj_samps, seq_to_samp_ddict):
        # then once we have compelted this for all sequences go clade by clade
        # and generate the sample order
        ordered_sample_list_by_uid = []
        sys.stdout.write('\nGoing clade by clade sorting by abundance\n')
        for clade in list('ABCDEFGHI'):
            sys.stdout.write(f'\rGetting clade {clade} seqs')
            tup_list_of_clade = []
            # get the clade specific list of the max_seq_ddict
            for k, v in max_seq_ddict.items():
                sys.stdout.write('\r{}'.format(k))
                if k.startswith(clade) or k[-2:] == '_{}'.format(clade):
                    tup_list_of_clade.append((k, v))

            if not tup_list_of_clade:
                continue
            # now get an ordered list of the sequences for this clade
            sys.stdout.write('\rOrdering clade {} seqs'.format(clade))

            ordered_sequence_of_clade_list = [x[0] for x in sorted(tup_list_of_clade, key=lambda x: x[1], reverse=True)]

            for seq_to_order_samples_by in ordered_sequence_of_clade_list:
                sys.stdout.write('\r{}'.format(seq_to_order_samples_by))
                tup_list_of_samples_that_had_sequence_as_most_abund = seq_to_samp_ddict[seq_to_order_samples_by]
                ordered_list_of_samples_for_seq_ordered = \
                    [x[0] for x in
                     sorted(tup_list_of_samples_that_had_sequence_as_most_abund, key=lambda x: x[1], reverse=True)]
                ordered_sample_list_by_uid.extend(ordered_list_of_samples_for_seq_ordered)
        # finally add in the samples that didn't have a maj sequence
        ordered_sample_list_by_uid.extend(no_maj_samps)
        return ordered_sample_list_by_uid

    def _generate_most_abundant_sequence_dictionaries(self, sequence_only_df_relative):
        # {sequence_name_found_to_be_most_abund_in_sample: num_samples_it_was_found_to_be_most_abund_in}
        max_seq_ddict = defaultdict(int)
        # {most_abundant_seq_name: [(dss.id, rel_abund_of_most_abund_seq) for samples with that seq as most abund]}
        seq_to_samp_ddict = defaultdict(list)
        # a list to hold the names of samples in which there was no most abundant sequence identified
        no_maj_samps = []
        for sample_to_sort_uid in sequence_only_df_relative.index.values.tolist():
            sys.stdout.write(f'\r{sample_to_sort_uid}: Getting maj seq for sample')
            sample_series_as_float = self._get_sample_seq_abund_info_as_pd_series_float_type(
                sample_to_sort_uid, sequence_only_df_relative)
            max_rel_abund = self._get_rel_abund_of_most_abund_seq(sample_series_as_float)
            if not max_rel_abund > 0:
                no_maj_samps.append(sample_to_sort_uid)
            else:
                max_abund_seq = self._get_name_of_most_abundant_seq(sample_series_as_float)
                # add a tup of sample name and rel abund of seq to the seq_to_samp_dict
                seq_to_samp_ddict[max_abund_seq].append((sample_to_sort_uid, max_rel_abund))
                # add this to the ddict count
                max_seq_ddict[max_abund_seq] += 1
        return max_seq_ddict, no_maj_samps, seq_to_samp_ddict

    @staticmethod
    def _get_sample_seq_abund_info_as_pd_series_float_type(sample_to_sort_uid, sequence_only_df_relative):
        return sequence_only_df_relative.loc[sample_to_sort_uid].astype('float')

    @staticmethod
    def _get_rel_abund_of_most_abund_seq(sample_series_as_float):
        return sample_series_as_float.max()

    @staticmethod
    def _get_name_of_most_abundant_seq(sample_series_as_float):
        max_abund_seq = sample_series_as_float.idxmax()
        return max_abund_seq

    def _create_ordered_output_dfs_from_series_with_sorted_sample_list(self):
        # NB I was originally performing the concat directly on the managedSampleOutputDict (i.e. the mp dict)
        # but this was starting to produce errors. Starting to work on the dss_id_to_pandas_series_results_list_dict
        #  (i.e. normal, not mp, dict) seems to not produce these errors.
        sys.stdout.write('\rPopulating the absolute dataframe with series. This could take a while...')
        output_df_absolute = pd.concat(
            [list_of_series[0] for list_of_series in
             self.dss_id_to_pandas_series_results_list_dict.values()], axis=1)
        sys.stdout.write('\rPopulating the relative dataframe with series. This could take a while...')
        output_df_relative = pd.concat(
            [list_of_series[1] for list_of_series in
             self.dss_id_to_pandas_series_results_list_dict.values()], axis=1)
        # now transpose
        output_df_absolute = output_df_absolute.T
        output_df_relative = output_df_relative.T
        # now make sure that the order is correct.
        self.output_df_absolute = output_df_absolute.reindex(self.sorted_sample_uid_list)
        self.output_df_relative = output_df_relative.reindex(self.sorted_sample_uid_list)

    def _check_sorted_sample_list_is_valid(self):
        if len(self.sorted_sample_uid_list) != len(self.list_of_dss_objects):
            raise RuntimeError({'message': 'Number of items in sorted_sample_list do not match those to be outputted!'})
        if self._smpls_in_sorted_smpl_list_not_in_list_of_samples():
            raise RuntimeError(
                {'message': 'Sample list passed in does not match sample list from db query'})

    def _smpls_in_sorted_smpl_list_not_in_list_of_samples(self):
        return list(
            set(self.sorted_sample_uid_list).difference(set([dss.id for dss in self.list_of_dss_objects])))

    def _generate_sample_output_series(self):
        """This generate a pandas series for each of the samples. It uses the ordered ReferenceSequence list created
         in the previous method as well as the other two dictionaries made.
         One df for absolute abundances and one for relative abundances. These series will be put together
         and ordered to construct the output data frames that will be written out for the user.
        """
        seq_count_table_output_series_generator_handler = SeqOutputSeriesGeneratorHandler(parent=self)
        seq_count_table_output_series_generator_handler.execute_sequence_count_table_dataframe_contructor_handler()
        self.dss_id_to_pandas_series_results_list_dict = \
            dict(seq_count_table_output_series_generator_handler.dss_id_to_pandas_series_results_list_mp_dict)

    def _collect_abundances_for_creating_the_output(self):
        seq_collection_handler = SequenceCountTableCollectAbundanceHandler(parent_seq_count_tab_creator=self)
        seq_collection_handler.execute_sequence_count_table_ordered_seqs_worker()
        # update the dictionaries that will be used in the second worker from the first worker
        self.update_dicts_for_the_second_worker_from_first_worker(seq_collection_handler)

    def update_dicts_for_the_second_worker_from_first_worker(self, seq_collection_handler):
        self.dss_id_to_list_of_dsss_objects_dict_mp_dict = \
            seq_collection_handler.dss_id_to_list_of_dsss_objects_mp_dict

        self.dss_id_to_list_of_abs_and_rel_abund_of_contained_dsss_dicts_mp_dict = \
            seq_collection_handler.\
                dss_id_to_list_of_abs_and_rel_abund_of_contained_dsss_dicts_mp_dict

        self.dss_id_to_list_of_abs_and_rel_abund_clade_summaries_of_noname_seqs_mp_dict = \
            seq_collection_handler.\
                dss_id_to_list_of_abs_and_rel_abund_clade_summaries_of_noname_seqs_mp_dict

        self.clade_abundance_ordered_ref_seq_list = \
            seq_collection_handler.clade_abundance_ordered_ref_seq_list


class SequenceCountTableCollectAbundanceHandler:
    """The purpose of this handler and the associated worker is to populate three dictionaries that will be used
    in making the count table output.
    1 - dict(ref_seq_name : cumulative relative abundance for each sequence across all samples)
    2 - sample_id : list(
                         dict(ref_seq_of_sample_name:absolute_abundance_of_dsss_in_sample),
                         dict(ref_seq_of_sample_name:relative_abundance_of_dsss_in_sample)
                         )
    3 - sample_id : list(
                         dict(clade:total_abund_of_no_name_seqs_of_clade_in_q_),
                         dict(clade:relative_abund_of_no_name_seqs_of_clade_in_q_)
                         )
    Abbreviations:
    ds = DataSet
    dss = DataSetSample
    dsss = DataSetSampleSequence
    ref_seq = ReferenceSeqeunce
    The end product of this method will be returned to the count table creator. The first dict will be used to create a
    list of the ReferenceSequence objects of this output ordered first by clade and then by cumulative relative
    abundance across all samples in the output.
    """
    def __init__(self, parent_seq_count_tab_creator):

        self.seq_count_table_creator = parent_seq_count_tab_creator
        self.mp_manager = Manager()
        self.input_dss_mp_queue = Queue()
        self._populate_input_dss_mp_queue()
        self.ref_seq_names_clade_annotated = [
        ref_seq.name if ref_seq.has_name else str(ref_seq.id) + '_{}'.format(ref_seq.clade) for
            ref_seq in self.seq_count_table_creator.ref_seqs_in_datasets]

        # TODO we were previously creating an MP dictionary for every proc used. We were then collecting them afterwards
        # I'm not sure if there was a good reason for doing this, but I don't see any comments to the contrary.
        # it should not be necessary to have a dict for every proc. Instead we can just have on mp dict.
        # we should check that this is still working as expected.
        # self.list_of_dictionaries_for_processes = self._generate_list_of_dicts_for_processes()
        self.dss_id_to_list_of_dsss_objects_mp_dict = self.mp_manager.dict()
        self._populate_dss_id_to_list_of_dsss_objects()
        self.annotated_dss_name_to_cummulative_rel_abund_mp_dict = self.mp_manager.dict(
            {refSeq_name: 0 for refSeq_name in self.ref_seq_names_clade_annotated})
        self.dss_id_to_list_of_abs_and_rel_abund_of_contained_dsss_dicts_mp_dict = self.mp_manager.dict()
        self.dss_id_to_list_of_abs_and_rel_abund_clade_summaries_of_noname_seqs_mp_dict = self.mp_manager.dict()

        # this is the list that we will use the self.annotated_dss_name_to_cummulative_rel_abund_mp_dict to create
        # it is a list of the ref_seqs_ordered first by clade then by abundance.
        self.clade_abundance_ordered_ref_seq_list = []

    def execute_sequence_count_table_ordered_seqs_worker(self):
        all_processes = []

        # close all connections to the db so that they are automatically recreated for each process
        # http://stackoverflow.com/questions/8242837/django-multiprocessing-and-database-connections
        db.connections.close_all()

        for n in range(self.seq_count_table_creator.num_proc):
            p = Process(target=self._sequence_count_table_ordered_seqs_worker, args=())
            all_processes.append(p)
            p.start()

        for p in all_processes:
            p.join()

        self._generate_clade_abundance_ordered_ref_seq_list_from_seq_name_abund_dict()

    def _generate_clade_abundance_ordered_ref_seq_list_from_seq_name_abund_dict(self):
        for i in range(len(self.seq_count_table_creator.ordered_list_of_clades_found)):
            temp_within_clade_list_for_sorting = []
            for seq_name, abund_val in self.annotated_dss_name_to_cummulative_rel_abund_mp_dict.items():
                if seq_name.startswith(
                        self.seq_count_table_creator.ordered_list_of_clades_found[i]) or seq_name[-2:] == \
                        f'_{self.seq_count_table_creator.ordered_list_of_clades_found[i]}':
                    # then this is a seq of the clade in Q and we should add to the temp list
                    temp_within_clade_list_for_sorting.append((seq_name, abund_val))
            # now sort the temp_within_clade_list_for_sorting and add to the cladeAbundanceOrderedRefSeqList
            sorted_within_clade = [
                a[0] for a in sorted(temp_within_clade_list_for_sorting, key=lambda x: x[1], reverse=True)]

            self.clade_abundance_ordered_ref_seq_list.extend(sorted_within_clade)

    def _sequence_count_table_ordered_seqs_worker(self):

        for dss in iter(self.input_dss_mp_queue.get, 'STOP'):
            sys.stdout.write(f'\r{dss.name}: collecting seq abundances')
            sequence_count_table_ordered_seqs_worker_instance = SequenceCountTableCollectAbundanceWorker(
                parent_handler=self, dss=dss)
            sequence_count_table_ordered_seqs_worker_instance.start_seq_abund_collection()

    def _populate_input_dss_mp_queue(self):
        for dss in self.seq_count_table_creator.list_of_dss_objects:
            self.input_dss_mp_queue.put(dss)

        for N in range(self.seq_count_table_creator.num_proc):
            self.input_dss_mp_queue.put('STOP')

    def _populate_dss_id_to_list_of_dsss_objects(self):
        for dss in self.seq_count_table_creator.list_of_dss_objects:
            sys.stdout.write(f'\r{dss.name}')
            self.dss_id_to_list_of_dsss_objects_mp_dict[dss.id] = list(
                DataSetSampleSequence.objects.filter(data_set_sample_from=dss))


class SequenceCountTableCollectAbundanceWorker:
    def __init__(self, parent_handler, dss):
        self.handler = parent_handler
        self.dss = dss
        self.total_abundance_of_sequences_in_sample = sum([int(a) for a in json.loads(self.dss.cladal_seq_totals)])

    def start_seq_abund_collection(self):
        clade_summary_absolute_dict, clade_summary_relative_dict = \
            self._generate_empty_noname_seq_abund_summary_by_clade_dicts()

        smple_seq_count_aboslute_dict, smple_seq_count_relative_dict = self._generate_empty_seq_name_to_abund_dicts()

        dsss_in_sample = self.handler.dss_id_to_list_of_dsss_objects_mp_dict[self.dss.id]

        for dsss in dsss_in_sample:
            # determine what the name of the seq will be in the output
            name_unit = self._determine_output_name_of_dsss_and_pop_noname_clade_dicts(
                clade_summary_absolute_dict, clade_summary_relative_dict, dsss)

            self._populate_abs_and_rel_abundances_for_dsss(dsss, name_unit, smple_seq_count_aboslute_dict,
                                                           smple_seq_count_relative_dict)

        self._associate_sample_abundances_to_mp_dicts(
            clade_summary_absolute_dict,
            clade_summary_relative_dict,
            smple_seq_count_aboslute_dict,
            smple_seq_count_relative_dict)

    def _associate_sample_abundances_to_mp_dicts(self, clade_summary_absolute_dict, clade_summary_relative_dict,
                                                 smple_seq_count_aboslute_dict, smple_seq_count_relative_dict):
        self.handler.dss_id_to_list_of_abs_and_rel_abund_of_contained_dsss_dicts_mp_dict[self.dss.id] = [smple_seq_count_aboslute_dict,
                                                                                                         smple_seq_count_relative_dict]
        self.handler.dss_id_to_list_of_abs_and_rel_abund_clade_summaries_of_noname_seqs_mp_dict[self.dss.id] = [
            clade_summary_absolute_dict, clade_summary_relative_dict]

    def _populate_abs_and_rel_abundances_for_dsss(self, dsss, name_unit, smple_seq_count_aboslute_dict,
                                                  smple_seq_count_relative_dict):
        rel_abund_of_dsss = dsss.abundance / self.total_abundance_of_sequences_in_sample
        self.handler.annotated_dss_name_to_cummulative_rel_abund_mp_dict[name_unit] += rel_abund_of_dsss
        smple_seq_count_aboslute_dict[name_unit] += dsss.abundance
        smple_seq_count_relative_dict[name_unit] += rel_abund_of_dsss

    def _determine_output_name_of_dsss_and_pop_noname_clade_dicts(
            self, clade_summary_absolute_dict, clade_summary_relative_dict, dsss):
        if not dsss.reference_sequence_of.has_name:
            name_unit = str(dsss.reference_sequence_of.id) + f'_{dsss.reference_sequence_of.clade}'
            # the clade summries are only for the noName seqs
            clade_summary_absolute_dict[dsss.reference_sequence_of.clade] += dsss.abundance
            clade_summary_relative_dict[
                dsss.reference_sequence_of.clade] += dsss.abundance / self.total_abundance_of_sequences_in_sample
        else:
            name_unit = dsss.reference_sequence_of.name
        return name_unit

    def _generate_empty_seq_name_to_abund_dicts(self):
        smple_seq_count_aboslute_dict = {seq_name: 0 for seq_name in self.handler.ref_seq_names_clade_annotated}
        smple_seq_count_relative_dict = {seq_name: 0 for seq_name in self.handler.ref_seq_names_clade_annotated}
        return smple_seq_count_aboslute_dict, smple_seq_count_relative_dict

    @staticmethod
    def _generate_empty_noname_seq_abund_summary_by_clade_dicts():
        clade_summary_absolute_dict = {clade: 0 for clade in list('ABCDEFGHI')}
        clade_summary_relative_dict = {clade: 0 for clade in list('ABCDEFGHI')}
        return clade_summary_absolute_dict, clade_summary_relative_dict


class SeqOutputSeriesGeneratorHandler:
    def __init__(self, parent):
        self.seq_count_table_creator = parent
        self.output_df_header = self._create_output_df_header()
        self.worker_manager = Manager()
        # dss.id : [pandas_series_for_absolute_abundace, pandas_series_for_absolute_abundace]
        self.dss_id_to_pandas_series_results_list_mp_dict = self.worker_manager.dict()
        self.dss_input_queue = Queue()
        self._populate_dss_input_queue()

    def execute_sequence_count_table_dataframe_contructor_handler(self):
        all_processes = []

        # close all connections to the db so that they are automatically recreated for each process
        # http://stackoverflow.com/questions/8242837/django-multiprocessing-and-database-connections
        db.connections.close_all()

        sys.stdout.write('\n\nOutputting seq data\n')
        for n in range(self.seq_count_table_creator.num_proc):
            p = Process(target=self._output_df_contructor_worker, args=())
            all_processes.append(p)
            p.start()

        for p in all_processes:
            p.join()

    def _output_df_contructor_worker(self):
        for dss in iter(self.dss_input_queue.get, 'STOP'):
            seq_output_series_generator_worker = SeqOutputSeriesGeneratorWorker(
                dss=dss,
                list_of_abs_and_rel_abund_clade_summaries_of_noname_seqs=self.seq_count_table_creator.dss_id_to_list_of_abs_and_rel_abund_clade_summaries_of_noname_seqs_mp_dict[dss.id],
                list_of_abs_and_rel_abund_of_contained_dsss_dicts=self.seq_count_table_creator.dss_id_to_list_of_abs_and_rel_abund_of_contained_dsss_dicts_mp_dict[dss.id],
                clade_abundance_ordered_ref_seq_list=self.seq_count_table_creator.clade_abundance_ordered_ref_seq_list,
                dss_id_to_pandas_series_results_list_mp_dict=self.dss_id_to_pandas_series_results_list_mp_dict,
                output_df_header=self.output_df_header)
            seq_output_series_generator_worker.make_series()

    def _populate_dss_input_queue(self):
        for dss in self.seq_count_table_creator.list_of_dss_objects:
            self.dss_input_queue.put(dss)

        for N in range(self.seq_count_table_creator.num_proc):
            self.dss_input_queue.put('STOP')

    def _create_output_df_header(self):
        header_pre = self.seq_count_table_creator.clade_abundance_ordered_ref_seq_list
        no_name_summary_strings = ['noName Clade {}'.format(cl) for cl in ['A', 'B', 'C', 'D', 'E', 'F', 'G', 'H', 'I']]
        qc_stats = [
            'raw_contigs', 'post_qc_absolute_seqs', 'post_qc_unique_seqs', 'post_taxa_id_absolute_symbiodinium_seqs',
            'post_taxa_id_unique_symbiodinium_seqs', 'size_screening_violation_absolute',
            'size_screening_violation_unique',
            'post_taxa_id_absolute_non_symbiodinium_seqs', 'post_taxa_id_unique_non_symbiodinium_seqs',
            'post_med_absolute',
            'post_med_unique']

        # append the noName sequences as individual sequence abundances
        return ['sample_name'] + qc_stats + no_name_summary_strings + header_pre


class SeqOutputSeriesGeneratorWorker:
    def __init__(
            self, dss, list_of_abs_and_rel_abund_clade_summaries_of_noname_seqs,
            list_of_abs_and_rel_abund_of_contained_dsss_dicts,
            dss_id_to_pandas_series_results_list_mp_dict, clade_abundance_ordered_ref_seq_list, output_df_header):

        self.dss = dss
        # dss.id : [{dsss:absolute abundance in dss}, {dsss:relative abundance in dss}]
        # dss.id : [{clade:total absolute abundance of no name seqs from that clade},
        #           {clade:total relative abundance of no name seqs from that clade}
        #          ]
        self.list_of_abs_and_rel_abund_clade_summaries_of_noname_seqs = list_of_abs_and_rel_abund_clade_summaries_of_noname_seqs
        self.list_of_abs_and_rel_abund_of_contained_dsss_dicts = list_of_abs_and_rel_abund_of_contained_dsss_dicts
        self.dss_id_to_pandas_series_results_list_mp_dict = dss_id_to_pandas_series_results_list_mp_dict
        self.clade_abundance_ordered_ref_seq_list = clade_abundance_ordered_ref_seq_list
        self.output_df_header = output_df_header
        self.sample_row_data_absolute = []
        self.sample_row_data_relative = []
        self.sample_seq_tot = sum([int(a) for a in json.loads(dss.cladal_seq_totals)])

    def make_series(self):
        sys.stdout.write(f'\r{self.dss.name}: Creating data ouput row')
        if self._dss_had_problem_in_processing():
            self.sample_row_data_absolute.append(self.dss.name)
            self.sample_row_data_relative.append(self.dss.name)

            self._populate_quality_control_data_of_failed_sample()

            self._output_the_failed_sample_pandas_series()
            return

        self._populate_quality_control_data_of_successful_sample()

        self._output_the_successful_sample_pandas_series()

    def _output_the_successful_sample_pandas_series(self):
        sample_series_absolute = pd.Series(self.sample_row_data_absolute, index=self.output_df_header, name=self.dss.id)
        sample_series_relative = pd.Series(self.sample_row_data_relative, index=self.output_df_header, name=self.dss.id)
        self.dss_id_to_pandas_series_results_list_mp_dict[self.dss.id] = [
            sample_series_absolute, sample_series_relative]

    def _populate_quality_control_data_of_successful_sample(self):
        # Here we add in the post qc and post-taxa id counts
        # For the absolute counts we will report the absolute seq number
        # For the relative counts we will report these as proportions of the sampleSeqTot.
        # I.e. we will have numbers larger than 1 for many of the values and the symbiodinium seqs should be 1
        self.sample_row_data_absolute.append(self.dss.name)
        self.sample_row_data_relative.append(self.dss.name)

        # CONTIGS
        # This is the absolute number of sequences after make.contigs
        contig_num = self.dss.num_contigs
        self.sample_row_data_absolute.append(contig_num)
        self.sample_row_data_relative.append(contig_num / self.sample_seq_tot)
        # POST-QC
        # store the aboslute number of sequences after sequencing QC at this stage
        post_qc_absolute = self.dss.post_qc_absolute_num_seqs
        self.sample_row_data_absolute.append(post_qc_absolute)
        self.sample_row_data_relative.append(post_qc_absolute / self.sample_seq_tot)
        # This is the unique number of sequences after the sequencing QC
        post_qc_unique = self.dss.post_qc_unique_num_seqs
        self.sample_row_data_absolute.append(post_qc_unique)
        self.sample_row_data_relative.append(post_qc_unique / self.sample_seq_tot)
        # POST TAXA-ID
        # Absolute number of sequences after sequencing QC and screening for Symbiodinium (i.e. Symbiodinium only)
        tax_id_symbiodinium_absolute = self.dss.absolute_num_sym_seqs
        self.sample_row_data_absolute.append(tax_id_symbiodinium_absolute)
        self.sample_row_data_relative.append(tax_id_symbiodinium_absolute / self.sample_seq_tot)
        # Same as above but the number of unique seqs
        tax_id_symbiodinium_unique = self.dss.unique_num_sym_seqs
        self.sample_row_data_absolute.append(tax_id_symbiodinium_unique)
        self.sample_row_data_relative.append(tax_id_symbiodinium_unique / self.sample_seq_tot)
        # store the absolute number of sequences lost to size cutoff violations
        size_violation_aboslute = self.dss.size_violation_absolute
        self.sample_row_data_absolute.append(size_violation_aboslute)
        self.sample_row_data_relative.append(size_violation_aboslute / self.sample_seq_tot)
        # store the unique size cutoff violations
        size_violation_unique = self.dss.size_violation_unique
        self.sample_row_data_absolute.append(size_violation_unique)
        self.sample_row_data_relative.append(size_violation_unique / self.sample_seq_tot)
        # store the abosolute number of sequenes that were not considered Symbiodinium
        tax_id_non_symbiodinum_abosulte = self.dss.non_sym_absolute_num_seqs
        self.sample_row_data_absolute.append(tax_id_non_symbiodinum_abosulte)
        self.sample_row_data_relative.append(tax_id_non_symbiodinum_abosulte / self.sample_seq_tot)
        # This is the number of unique sequences that were not considered Symbiodinium
        tax_id_non_symbiodinium_unique = self.dss.non_sym_unique_num_seqs
        self.sample_row_data_absolute.append(tax_id_non_symbiodinium_unique)
        self.sample_row_data_relative.append(tax_id_non_symbiodinium_unique / self.sample_seq_tot)
        # Post MED absolute
        post_med_absolute = self.dss.post_med_absolute
        self.sample_row_data_absolute.append(post_med_absolute)
        self.sample_row_data_relative.append(post_med_absolute / self.sample_seq_tot)
        # Post MED unique
        post_med_unique = self.dss.post_med_unique
        self.sample_row_data_absolute.append(post_med_unique)
        self.sample_row_data_relative.append(post_med_unique / self.sample_seq_tot)

        # now add the clade divided summaries of the clades
        for clade in list('ABCDEFGHI'):
            self.sample_row_data_absolute.append(
                self.list_of_abs_and_rel_abund_clade_summaries_of_noname_seqs[0][clade])
            self.sample_row_data_relative.append(
                self.list_of_abs_and_rel_abund_clade_summaries_of_noname_seqs[1][clade])

        # and append these abundances in order of cladeAbundanceOrderedRefSeqList to
        # the sampleRowDataCounts and the sampleRowDataProps
        for seq_name in self.clade_abundance_ordered_ref_seq_list:
            sys.stdout.write('\rOutputting seq data for {}: sequence {}'.format(self.dss.name, seq_name))
            self.sample_row_data_absolute.append(
                self.list_of_abs_and_rel_abund_of_contained_dsss_dicts[0][seq_name])
            self.sample_row_data_relative.append(
                self.list_of_abs_and_rel_abund_of_contained_dsss_dicts[1][seq_name])

    def _output_the_failed_sample_pandas_series(self):
        sample_series_absolute = pd.Series(self.sample_row_data_absolute, index=self.output_df_header, name=self.dss.id)
        sample_series_relative = pd.Series(self.sample_row_data_relative, index=self.output_df_header, name=self.dss.id)
        self.dss_id_to_pandas_series_results_list_mp_dict[self.dss.id] = [sample_series_absolute,
                                                                                  sample_series_relative]

    def _populate_quality_control_data_of_failed_sample(self):
        # Add in the qc totals if possible
        # For the proportions we will have to add zeros as we cannot do proportions
        # CONTIGS
        # This is the absolute number of sequences after make.contigs

        if self.dss.num_contigs:
            contig_num = self.dss.num_contigs
            self.sample_row_data_absolute.append(contig_num)
            self.sample_row_data_relative.append(0)
        else:
            self.sample_row_data_absolute.append(0)
            self.sample_row_data_relative.append(0)
        # POST-QC
        # store the aboslute number of sequences after sequencing QC at this stage
        if self.dss.post_qc_absolute_num_seqs:
            post_qc_absolute = self.dss.post_qc_absolute_num_seqs
            self.sample_row_data_absolute.append(post_qc_absolute)
            self.sample_row_data_relative.append(0)
        else:
            self.sample_row_data_absolute.append(0)
            self.sample_row_data_relative.append(0)
        # This is the unique number of sequences after the sequencing QC
        if self.dss.post_qc_unique_num_seqs:
            post_qc_unique = self.dss.post_qc_unique_num_seqs
            self.sample_row_data_absolute.append(post_qc_unique)
            self.sample_row_data_relative.append(0)
        else:
            self.sample_row_data_absolute.append(0)
            self.sample_row_data_relative.append(0)
        # POST TAXA-ID
        # Absolute number of sequences after sequencing QC and screening for Symbiodinium (i.e. Symbiodinium only)
        if self.dss.absolute_num_sym_seqs:
            tax_id_symbiodinium_absolute = self.dss.absolute_num_sym_seqs
            self.sample_row_data_absolute.append(tax_id_symbiodinium_absolute)
            self.sample_row_data_relative.append(0)
        else:
            self.sample_row_data_absolute.append(0)
            self.sample_row_data_relative.append(0)
        # Same as above but the number of unique seqs
        if self.dss.unique_num_sym_seqs:
            tax_id_symbiodinium_unique = self.dss.unique_num_sym_seqs
            self.sample_row_data_absolute.append(tax_id_symbiodinium_unique)
            self.sample_row_data_relative.append(0)
        else:
            self.sample_row_data_absolute.append(0)
            self.sample_row_data_relative.append(0)
        # size violation absolute
        if self.dss.size_violation_absolute:
            size_viol_ab = self.dss.size_violation_absolute
            self.sample_row_data_absolute.append(size_viol_ab)
            self.sample_row_data_relative.append(0)
        else:
            self.sample_row_data_absolute.append(0)
            self.sample_row_data_relative.append(0)
        # size violation unique
        if self.dss.size_violation_unique:
            size_viol_uni = self.dss.size_violation_unique
            self.sample_row_data_absolute.append(size_viol_uni)
            self.sample_row_data_relative.append(0)
        else:
            self.sample_row_data_absolute.append(0)
            self.sample_row_data_relative.append(0)
        # store the abosolute number of sequenes that were not considered Symbiodinium
        if self.dss.non_sym_absolute_num_seqs:
            tax_id_non_symbiodinum_abosulte = self.dss.non_sym_absolute_num_seqs
            self.sample_row_data_absolute.append(tax_id_non_symbiodinum_abosulte)
            self.sample_row_data_relative.append(0)
        else:
            self.sample_row_data_absolute.append(0)
            self.sample_row_data_relative.append(0)
        # This is the number of unique sequences that were not considered Symbiodinium
        if self.dss.non_sym_unique_num_seqs:
            tax_id_non_symbiodinium_unique = self.dss.non_sym_unique_num_seqs
            self.sample_row_data_absolute.append(tax_id_non_symbiodinium_unique)
            self.sample_row_data_relative.append(0)
        else:
            self.sample_row_data_absolute.append(0)
            self.sample_row_data_relative.append(0)
        # post-med absolute
        if self.dss.post_med_absolute:
            post_med_abs = self.dss.post_med_absolute
            self.sample_row_data_absolute.append(post_med_abs)
            self.sample_row_data_relative.append(0)
        else:
            self.sample_row_data_absolute.append(0)
            self.sample_row_data_relative.append(0)
        # post-med absolute
        if self.dss.post_med_unique:
            post_med_uni = self.dss.post_med_unique
            self.sample_row_data_absolute.append(post_med_uni)
            self.sample_row_data_relative.append(0)
        else:
            self.sample_row_data_absolute.append(0)
            self.sample_row_data_relative.append(0)

        # no name clade summaries get 0.
        for _ in list('ABCDEFGHI'):
            self.sample_row_data_absolute.append(0)
            self.sample_row_data_relative.append(0)

        # All sequences get 0s
        for _ in self.clade_abundance_ordered_ref_seq_list:
            self.sample_row_data_absolute.append(0)
            self.sample_row_data_relative.append(0)

    def _dss_had_problem_in_processing(self):
        return self.dss.error_in_processing or self.sample_seq_tot == 0
