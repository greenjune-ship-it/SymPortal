#!/usr/bin/env python3
"""This script will be run as a chron job
It will look for submissions that have a status of framework_loading_complete, have no error status, and have
a for_analysis of True.
The DataSet objects associated with each of these submission will be collected and added to a
data base version analyis (DBV) using the SymPortal framework --analyse_next flag.
This analysis will be run without outputs.
Once the analysis is complete the status of the Submission object will be udated so that its progress can be reflected
on the SymPortal.org website. However, the script will continue on and output the results as well. The status
will be set to framework_analysis_complete

Once the analysis is complete, each of the newly submitted datasets will be output via their associated Study objects
using the framework --output_study_from_analysis flag.

One the output is complete the status of the Submission object will be set to framework_output_complete.
The framework_results_dir_path attribute of the submission object will be set. This, combined with a True for_analysis
will be the keys for the next chron job to transfer these files over to the symportal.org server.
"""

import sys
import subprocess
import platform
import os
import main
from datetime import datetime
sys.path.append("..")
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "settings")
from django.core.wsgi import get_wsgi_application
application = get_wsgi_application()
from dbApp.models import Submission, DataAnalysis


class ChronAnalysis:
    def __init__(self):
        self._check_no_other_instance_running()
        self.submission_objects = Submission.objects.filter(
                    progress_status="framework_loading_complete",
                    error_has_occured=False,
                    for_analysis=True
                ).all()
        self.dataset_objects = [
            s.associated_dataset for s in self.submission_objects]
        self.dataset_string = ','.join([str(d.id) for d in self.dataset_objects])
        self.dt_str = self._get_date_time()
        self.analysis_name = f'{self.dt_str}_DBV'
        # number of proc will be 30
        # However when running this on the mac for debug we will pull this down to 4
        if platform.system() == 'Linux':
            self.num_proc = 30
        else:
            self.num_proc = 4
        # Dynamics
        self.work_flow_manager = None

    def output(self):
        """
        For each of the submission objects, output the results from the analysis that was just completed
        """
        # Refresh the submission objects so that the output will still be completed even if there was not
        # an analysis to run. E.g. the script could have died after the analysis was completed.
        self.submission_objects = Submission.objects.filter(
            progress_status="framework_analysis_complete",
            error_has_occured=False,
            for_analysis=True
        ).all()


        for sub_obj in self.submission_objects:
            # Because we don't want to rely on the fact that an anaysis was conduced above, we will grab the latest
            # DataAnalysis that contains the Study/DataSet objects associated with it.
            latest_dataanalysis = list(DataAnalysis.objects.filter(
                list_of_data_set_uids__contains=str(sub_obj.associated_dataset.id)).order_by('-id'))[0]
            study_id_str = str(sub_obj.associated_study.id)
            custom_args_list = [
                '--output_study_from_analysis', study_id_str, '--num_proc', str(self.num_proc),
                '--data_analysis_id', str(latest_dataanalysis.id),
            ]
            try:
                self.work_flow_manager = main.SymPortalWorkFlowManager(custom_args_list)
                sub_obj.study_output_started_date_time = self.work_flow_manager.date_time_str
                sub_obj.save()
                self.work_flow_manager.start_work_flow()
            except Exception as e:
                # TODO handle errors for Submission objects and chron jobs
                print(e)
                raise NotImplementedError(
                    f'An error has occured while trying to output results for {sub_obj.name}.'
                )

            # Here the output is complete
            # Log the complete time and the output directory on the framework server
            sub_obj.study_output_complete_date_time = self._get_date_time()
            sub_obj.framework_results_dir_path = self.work_flow_manager.output_dir
            sub_obj.progress_status = "framework_output_complete"
            sub_obj.save()

    def analyse(self):
        """
        Run a SymPortal analysis that includes all of the DataSets of the preivous analysis plus the
        DataSet/Study objects associated with the self.submission_objects
        """

        custom_args_list = [
            '--analyse_next', self.dataset_string, '--num_proc', str(self.num_proc), '--no_output',
            '--name', self.analysis_name
        ]

        try:
            # Run the analysis
            self.work_flow_manager = main.SymPortalWorkFlowManager(custom_args_list)
            # Log the start time of the analysis
            for sub_obj in self.submission_objects:
                sub_obj.analysis_started_date_time = self.work_flow_manager.date_time_str
                sub_obj.save()
            self.work_flow_manager.start_work_flow()
        except Exception as e:
            # TODO handle errors for Submission objects and chron jobs
            print(e)
            raise NotImplementedError(
                'An error has occured while trying to analyse the current batch of Study objects.'
            )

        # At this point the analysis is complete
        # Update the status and analysis complete attribute then move on to outputting each submission
        for sub_obj in self.submission_objects:
            sub_obj.progress_status = 'framework_analysis_complete'
            sub_obj.analysis_complete_date_time = self._get_date_time()
            sub_obj.save()

    @staticmethod
    def _check_no_other_instance_running():
        try:
            if sys.argv[1] == 'debug':  # For development only
                pass
            else:
                raise RuntimeError('Unknown arg at sys.argv[1]')
        except IndexError:
            captured_output = subprocess.run(['pgrep', '-f', 'chron_loading.py'], capture_output=True)
            if captured_output.returncode == 0:  # PIDs were returned
                procs = captured_output.stdout.decode('UTF-8').rstrip().split('\n')
                if platform.system() == 'Linux':
                    # Then we expect there to be one PID for the current process
                    if len(procs) > 1:
                        sys.exit()
                else:
                    # Then we are likely on mac and we expect no PIDs
                    sys.exit()
            else:
                # No PIDs returned
                pass

    @staticmethod
    def _get_date_time():
        return str(
            datetime.utcnow()
        ).split('.')[0].replace('-', '').replace(' ', 'T').replace(':', '')

ca = ChronAnalysis()
if ca.submission_objects:
    ca.analyse()
ca.output()