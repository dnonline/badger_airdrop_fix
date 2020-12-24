import json
import statistics
from rich.console import Console

console = Console()
from brownie import *


"""
Each user should get a minimum amount.
    - Number of addresses 
    - Minimum
    - Maximum
    - Average
    - Median
    - Total
    - Chart of distributions?
"""


def to_list(data):
    list = []
    for value in data.values():
        list.append(value)
    return list


def get_stats(list):
    return {
        "total": sum(list),
        "startTotal": sum(list),
        "mean": statistics.mean(list),
        "median": statistics.median(list),
        "highest": max(list),
        "lowest": min(list),
    }


def smooth(data):
    # f = open("snapshot/final.json",)
    # data = json.load(f)

    print("Processing values to list")

    list = to_list(data)

    threshold = Wei("20 ether")
    smoothed = data

    initial_stats = get_stats(list)
    startTotal = initial_stats["total"]

    # Bring everyone below threshold to threshold
    for key, value in smoothed.items():
        if value <= threshold:
            smoothed[key] = threshold
            print("Bringing {} to threshold {}".format(value / 1e18, threshold / 1e18))

    temp_list = to_list(smoothed)
    temp_stats = get_stats(temp_list)
    if_everyone_had_20 = len(list) * 20

    totalToRemove = sum(temp_list) - startTotal

    threshold_to_smooth = threshold + Wei("5 ether")

    above_20 = []
    for key, value in smoothed.items():
        if value > threshold_to_smooth:
            above_20.append(value)

    sum_above_20 = sum(above_20)

    # Reduce every item above threshold proportionally
    for key, value in smoothed.items():
        if value > threshold_to_smooth:
            proportionOfExcess = value / sum_above_20
            toRemove = totalToRemove * proportionOfExcess
            newValue = int(value - toRemove)
            print(
                "Removing {} ({}%) from value {} -> {}".format(
                    toRemove / 1e18, proportionOfExcess, value / 1e18, newValue / 1e18,
                )
            )
            smoothed[key] = newValue

    end_list = to_list(smoothed)
    end_sum = sum(end_list)
    end_stats = get_stats(end_list)
    print(
        "Temp total is {}, Tokens To Remove: {}".format(
            temp_stats["total"], totalToRemove / 1e18
        )
    )
    print("There is {} value among tokens".format(sum_above_20 / 1e18))

    # sum average mean median
    console.print("[bold cyan]===== Airdrop Stats =====[/bold cyan]")
    console.log("sum: ", initial_stats["total"] / 1e18)
    console.log("mean: ", initial_stats["mean"] / 1e18)
    console.log("median: ", initial_stats["median"] / 1e18)
    console.log("max: ", initial_stats["highest"] / 1e18)
    console.log(
        "min: ", initial_stats["lowest"] / 1e18, " (In Wei: )", initial_stats["lowest"]
    )
    console.log("total recipients: ", len(list))

    console.log("if_everyone_had_20: ", if_everyone_had_20)
    console.log("total distributed: ", initial_stats["total"] / 1e18)
    console.log(
        "total to add: ", (Wei("2100000 ether") - initial_stats["total"]) / 1e18
    )

    console.print("[bold green]===== Temp Stats =====[/bold green]")
    console.log("sum: ", temp_stats["total"] / 1e18)
    console.log("mean: ", temp_stats["mean"] / 1e18)
    console.log("median: ", temp_stats["median"] / 1e18)
    console.log("max: ", temp_stats["highest"] / 1e18)
    console.log(
        "min: ", temp_stats["lowest"] / 1e18, " (In Wei: )", temp_stats["lowest"]
    )
    console.log("total recipients: ", len(temp_list))

    console.log("if_everyone_had_20: ", if_everyone_had_20)
    console.log("total distributed: ", end_stats["total"] / 1e18)
    console.log("total to add: ", (Wei("2100000 ether") - end_stats["total"]) / 1e18)

    console.print("[bold yellow]===== After Modification Stats =====[/bold yellow]")
    console.log("sum: ", end_stats["total"] / 1e18)
    console.log("mean: ", end_stats["mean"] / 1e18)
    console.log("median: ", end_stats["median"] / 1e18)
    console.log("max: ", end_stats["highest"] / 1e18)
    console.log("min: ", end_stats["lowest"] / 1e18, " (In Wei: )", end_stats["lowest"])
    console.log("total recipients: ", len(end_list))

    console.log("if_everyone_had_20: ", if_everyone_had_20)
    console.log("total distributed: ", end_stats["total"] / 1e18)
    console.log("total to add: ", (Wei("2100000 ether") - end_stats["total"]) / 1e18)

    return smoothed

    """
    How to even out airdrop:
    * Give everyone 1 extra coin to put us over the 2,100,000 total
    * Bump everyone under 20 coins up to 20
    * Figure out how many coins over 2,100,000 we are
    * Scale down from everyone >20 coins in proportion to their weight of "excess coins"
    * The more "excess coins" you have, the more you lose

    What's the total of excess coins?
    How many excess coins to you have?
    What proportion of excess coins to you have?
    You lose <proportion of excess coins> * excess coins
    """
