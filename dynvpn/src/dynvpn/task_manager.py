
import asyncio
import traceback

from typing import Coroutine,  List, Callable, Awaitable

from dataclasses import dataclass

import logging

@dataclass
class task_wrapper():
    task : asyncio.Task
    wait_task : asyncio.Task

    def __hash__(self):
        return self.task.get_name()


class task_manager():

    def __init__(self, node, logger : logging.Logger): 

        # keep the list of task names separately to avoid dict iterator invalidation
        # they are kept consistent, and we iterate over the list
        self.tasks_dict=dict()
        self.tasks_list=list()

        # TODO when tasks are redesigned this reference to the node class will not be necessary
        self.node = node

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
    actually handles task exit/cancellation for the given task

    returns True if the task exited "normally" (without any uncaught exception), False otherwise
    returns None if we encountered an exception outside of the task
    """
    async def _handle(self, t : asyncio.Task):
        exited_noexc=None
        try:

            # retaining original multi-wait invocation in case we need to handle multiple
            # tasks here again
            done, _=await asyncio.wait(
                [ t ], return_when=asyncio.FIRST_COMPLETED
            )
            for t in done:
                tname=t.get_name()
                try:
                    if e := t.exception():
                        self._logger.error(f'task {tname} encountered an exception: ')
                        self._logger.error(traceback.format_exception(e))
                        exited_noexc=False
                        
                except asyncio.CancelledError:
                    self._logger.info(f'task {tname} was cancelled')

                try:
                    self._logger.info(f'task {tname} ended')
                except KeyError:
                    self._logger.error('task ended but not present in self.tasks')

                # removal here won't invalidate the list iterator, and we keep tasks_dict
                # and tasks_list consistent
                self.tasks_list.remove(tname) 
                del self.tasks_dict[tname]

                # for now, manually check each VPN lock to see if this task locked it
                # later, this will be improved when we contain each task in a unified "dynvpn_task"
                # class that provides access to context. contextvars.Context does not appear to be 
                # satisfactory for our use case
                for _, site in self.node.sites.items():
                    for _, vpn in site.vpn.items():
                        if vpn.lock is not None and vpn.lock.locked_task == tname:
                            vpn.lock.unlock(force=True)


                exited_noexc=True

            return exited_noexc
                
        except Exception as e:
            self._logger.error(traceback.format_exc())
            return None


    """
    wrap `f` in a task and manage it with the task_manager (using _handle, above)

    returns an async awaitable wrapper for f 
    f's 
    """
    def add(self, f : Coroutine, tname) -> Coroutine:
        task=asyncio.create_task(f, name=tname)

        wait_task=asyncio.create_task(self._handle(task), name=f'{tname}_wait-task')
        #self._logger.debug(f'task_manager.add: created task {tname} id={id(task)} waiter task id={id(wait_task)}')

        tobj=task_wrapper(
            wait_task=wait_task,
            task=task
        )
        self.tasks_dict[tname]=tobj
        self.tasks_list.append(tname)

        return wait_task

    def list(self):
        return self.tasks_list.copy()

    def find(self, tname : str):
        try:
            return self.tasks_dict[tname].task
        except KeyError:
            if tname in self.tasks_list:
                self._logger.error(f'task_manager.find({tname}): inconsistent state: dict={self.tasks_dict.keys()} list={self.tasks_list}')
            return None


    """
    run the same coroutine several times in parallel, with each invocation given a different argument
    as provided from a list (`items`), and manage the tasks using the task_manager instance

    each value in `items` needs to be convertible to string
    """
    async def iter_add_wait(self, items : list, f : Callable[[str], Awaitable], name):

        def child_name(item):
            return name + ':' + str(item)

        wait_tasks={}

        # client can wait for the entire sequence to terminate
        for item in items:
            wait_tasks[item]=self.add(f(item), child_name(item))

        for item, wt in wait_tasks.items():
            await wt

        return 


        