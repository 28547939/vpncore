
import asyncio
import traceback

from typing import Coroutine,  List

from dataclasses import dataclass
import logging

@dataclass
class task_wrapper():
    task : asyncio.Task
    wait_task : asyncio.Task

    def __hash__(self):
        return self.task.get_name()


class task_manager():

    def __init__(self, logger : logging.Logger): 

        # keep the list of task names separately to avoid dict iterator invalidation
        # they are kept consistent, and we iterate over the list
        self.tasks_dict=dict()
        self.tasks_list=list()

        self._logger=logger



    """
    just ensures that all _handle awaitables are awaited
    """
    async def run(self):
        while len(self.tasks_list) > 0:
            for tname in self.tasks_list:
                tobj=self.tasks_dict[tname]
                try:
                    await tobj.wait_task

                except Exception as e: 
                    self._logger.error(traceback.format_exc()) # TODO confirm this syntax



    """
    actually handles task exit/cancellation for the given 
    """
    async def _handle(self, t : asyncio.Task):
        # retaining original multi-wait invocation in case we need to handle multiple
        # tasks here again
        try:
            done, _=await asyncio.wait(
                [ t ], return_when=asyncio.FIRST_COMPLETED
            )
            for t in done:
                tname=t.get_name()
                try:
                    if e := t.exception():
                        raise e
                except asyncio.CancelledError:
                    self._logger.info(f'task {tname} was cancelled')
                except Exception as e:
                    self._logger.error(f'task {tname} encountered an exception: ')
                    self._logger.error(traceback.format_exc())

                try:
                    self._logger.info(f'task {tname} ended')
                except KeyError:
                    self._logger.error('task ended but not present in self.tasks')

                # removal here won't invalidate the list iterator, and we keep tasks_dict
                # and tasks_list consistent
                self.tasks_list.remove(tname) 
                del self.tasks_dict[tname]
                
        except Exception as e:
            self._logger.error(traceback.format_exc())

    def add(self, f : Coroutine, tname):
        task=asyncio.create_task(f, name=tname)
        wait_task=asyncio.create_task(self._handle(task), name=f'{tname}_wait-task')
        tobj=task_wrapper(
            wait_task=wait_task,
            task=task
        )
        self.tasks_dict[tname]=tobj
        self.tasks_list.append(tname)

    def list(self):
        return self.tasks_list.copy()

    def find(self, tname : str):
        try:
            return self.tasks_dict[tname].task
        except KeyError:
            return None
            #self._logger.error(f'task_manager.get called on non-existent task {tname}')