from celery import task
from oms_pds.pds.models import Profile, Notification, Device
from bson import ObjectId
from pymongo import Connection
from django.conf import settings
import time
from datetime import date, timedelta
import json
import pdb
import math
import cluster
import random
from gcm import GCM

from oms_pds.pds.models import Profile

"""the MONGODB_DATABASE_MULTIPDS setting is set by extract-user-middleware in cases where we need multiple PDS instances within one PDS service """


connection = Connection(
    host=getattr(settings, "MONGODB_HOST", None),
    port=getattr(settings, "MONGODB_PORT", None)
)

#def computeActivityScore(activityList):
#    recentActiveTotals = [item["low"] + item["high"] for item in activityList]
#    return  min(math.log(2 + sum(recentActiveTotals) / 50.0, 2) - 1, 10)

def computeActivityScore(activityList):
    recentActiveTotals = [item["low"] + item["high"] for item in activityList]
    recentTotal = float(sum([item["total"] for item in activityList]))

    factor = 1000.0 / recentTotal if recentTotal > 0 else 1
    #factor = baselineTotal / recentTotal if recentTotal > 0 else 1
#    return min(1 / (1 + math.exp(center - sum(recentActiveTotals))), 10)
#    return  min(math.log(2 + sum(factor * recentActiveTotals) / 5.0, 2) - 1, 10)
    return min(5 * (1 + math.erf((factor * float(sum(recentActiveTotals)) - 77) / 39.52)), 10)


def computeFocusScore(focusList):
    recentTotals = [item["focus"] for item in focusList]
    return min(math.log(1 + sum(recentTotals), 2), 10)   

def computeSocialScore(socialList):
    recentTotals = [item["social"] for item in socialList]
    return min(math.log(1 + sum(recentTotals), 2), 10)

def intervalsOverlap(i1, i2):
    return i2[0] <= i1[0] <= i2[1] or i2[0] <= i1[1] <= i2[1] or i1[0] <= i2[0] <= i1[1] or i1[0] <= i2[1] <= i1[1]


def selfAssessedActivityForTimeRange(collection, start, end, answerlistCollection=None):
    verificationEntries = collection.find({ "key": "ActivityVerification"})
    past3DaysEntries = collection.find({ "key": "ActivityPast3Days" })
    oneDay = 24 * 3600    

    v = []

    if verificationEntries.count() > 0:
        for answerList in verificationEntries:
            v.extend([value for value in answerList["value"]])
    
    p = []
    if past3DaysEntries.count() > 0:
        for answerList in past3DaysEntries:
            p.extend([value for value in answerList["value"]])

    v = [value for value in v if intervalsOverlap([value["time"] - oneDay, value["time"]], [start, end])]
    p = [value for value in p if intervalsOverlap([value["time"] - 3*oneDay, value["time"]], [start, end])]
    #pdb.set_trace()
    v.extend(p)
   
    allScores = [int(value["value"]) for value in v]
  
    average = sum(allScores) / len(allScores) if len(allScores) > 0 else 0

    return {"start": start, "end": end, "activity": average * 2.0 }


def activityForTimeRange(collection, start, end, answerlistCollection=None):
    lowActivityIntervals = highActivityIntervals = totalIntervals = 0
    
    activityEntries = collection.find({ "key": { "$regex" : "ActivityProbe$" }, "time": { "$gte" : start, "$lt":end }})
    #print activityEntries.count()
    if activityEntries.count() > 0:
        for data in activityEntries:
            #pdb.set_trace()
            dataValue = data["value"]
            if ("total_intervals" in dataValue):
                totalIntervals += dataValue["total_intervals"]
                lowActivityIntervals += dataValue["low_activity_intervals"]
                highActivityIntervals += dataValue["high_activity_intervals"]
            else:
                totalIntervals += 1
                lowActivityIntervals += 1 if dataValue["activitylevel"] == "low" else 0
                highActivityIntervals += 1 if dataValue["activitylevel"] == "high" else 0
       
        activity =  { "start": start, "end": end, "total": totalIntervals, "low": lowActivityIntervals, "high": highActivityIntervals }
 
        if answerlistCollection is not None:
            selfAssessed = selfAssessedActivityForTimeRange(answerlistCollection, start, end)
            activity["self"] = selfAssessed["activity"]

        return activity
    return None

def focusForTimeRange(collection, start, end, answerlistCollection=None):
    screenOnCount = 0
    
    screenOnEntries = collection.find({ "key": { "$regex": "ScreenProbe$" }, "time": {"$gte": start, "$lt": end }})
    if screenOnEntries.count() > 0:
        for data in screenOnEntries:
            dataValue = data["value"]
            screenOnCount += 1 if dataValue["screen_on"] else 0    
        return { "start": start, "end": end, "focus": screenOnCount }
    return None

def socialForTimeRange(collection, start, end, answerlistCollection=None):
    score = 0
    
    # For now, we're just taking the most recent probe value and checking message / call dates within it
    # This will not account for messages or call log entries that might have been deleted.
    
    # NOTE: we're including the start date in the queries below to simply shrink the number of entries we need to sort
    # Given that SMS and call log probes may include all messages and calls stored on the phone, we can't just look
    # at entries collected during that time frame
    
#    smsEntries = collection.find({ "key": { "$regex": "SMSProbe$" }, "time": {"$gte": start}})
#    
#    if smsEntries.count() > 0:
#        # Message times are recorded at the millisecond level. It should be safe to use that as a unique id for messages
#        messageSets = [smsEntry["value"]["messages"] for smsEntry in smsEntries]
#        messages = set([message for messageSet in messageSets for message in messageSet if message["date"] >= start*1000 and message["date"] < end*1000])
#        
#        # We're assuming a hit on a thread is equivalent to a single phone call
#        messageCountByThread = {}
#        
#        for threadId in [message["thread_id"] for message in messages]:
#            messageCountByAddress[threadId] = len([message for message in messages if message["threadId"] if message["thread_id"] = ])
#        #messageTimes = set([message["date"] for message in messages if message["date"] >= start*1000 and message["date"] < end*1000])
#        smsCount = len(messageTimes)
    #print collection
    #pdb.set_trace()
    callLogEntries = collection.find({ "key": { "$regex": "CallLogProbe$" }, "time": {"$gte": start}})
    
    if callLogEntries.count() > 0:
        callSets = [callEntry["value"] for callEntry in callLogEntries]
        #v4 = "calls" not in callSets[0]
        callSets = [value["calls"] if "calls" in value else value for value in callSets]
        calls = []
        for callSet in callSets:
            if isinstance(callSet, list):
                calls.extend(callSet)
            else:
                calls.append(callSet)
        calls = [call for call in calls if call["date"] >= start*1000 and call["date"] < end*1000]
        
        #callTimes = set([call["date"] for call in calls if call["date"] >= start*1000 and call["date"] < end*1000])
        countsByNumber = [float(len([call for call in calls if call["number"] == numberHash])) for numberHash in set([call["number"] for call in calls])]
        totalCalls = sum(countsByNumber)
        frequencies = [count / totalCalls for count in countsByNumber]
        score =sum([-frequency * math.log(frequency, 10) for frequency in frequencies]) * 10
    
       	return { "start": start, "end": end, "social": score}
    return None

def aggregateForAllUsers(answerKey, timeRanges, aggregator, collectionName="funf"):
    profiles = Profile.objects.all()
    aggregates = {}

    for profile in profiles:
        data = aggregateForUser(profile, answerKey, timeRanges, aggregator, collectionName)
        if data is not None and len(data) > 0:
            aggregates[profile.uuid] = data

    return aggregates

def aggregateForUser(profile, answerKey, timeRanges, aggregator, collectionName="funf"):
    aggregates = []
    
    #print profile.uuid
    #pdb.set_trace()
    dbName = "User_" + str(profile.id)
    collection = connection[dbName][collectionName]
    answerlistCollection = connection[dbName]["answerlist"]
    
    for (start, end) in timeRanges:
        data = aggregator(collection, start, end)
        if data is not None:
            aggregates.append(aggregator(collection, start, end))
    
    if answerKey is not None:
        answer = answerlistCollection.find({ "key" : answerKey })
    
        if answer.count() == 0:
            answer = { "key": answerKey }
        else:
            answer = answer[0]
            
        answer["value"] = aggregates
        
        answerlistCollection.save(answer)

    return aggregates

def getStartTime(daysAgo, startAtMidnight):
    currentTime = time.time()
    return time.mktime((date.fromtimestamp(currentTime) - timedelta(days=daysAgo)).timetuple()) if startAtMidnight else currentTime - daysAgo * 24 * 3600
def recentSelfAssessedActivityLevels():
    startTime = getStartTime(6, True)
    endTime = time.time()
    interval = 3600 * 4
    answerKey = "SelfAssessedRecentActivityByHour"
    timeRanges = [(start, start + interval) for start in range(int(startTime), int(endTime), interval)]

    return aggregateForAllUsers(answerKey, timeRanges, selfAssessedActivityForTimeRange, "answerlist")

def recentActivityLevels():
    startTime = getStartTime(6, True)
    endTime = time.time()
    interval = 3600*4
    answerKey = "RecentActivityByHour"
    timeRanges = [(start, start + interval) for start in range(int(startTime), int(endTime), interval)]

    return aggregateForAllUsers(answerKey, timeRanges, activityForTimeRange)

def recentFocusLevels():
    currentTime = time.time()
    answerKey = "RecentFocusByHour"
    today = date.fromtimestamp(currentTime)
    startTime = time.mktime((today - timedelta(days=6)).timetuple())
    timeRanges = [(start, start + 3600*4) for start in range(int(startTime), int(currentTime), 3600*4)]
   
    return aggregateForAllUsers(answerKey, timeRanges, focusForTimeRange)

def recentSocialLevels():
    currentTime = time.time()
    answerKey = "RecentSocialByHour"
    today = date.fromtimestamp(currentTime)
    startTime = time.mktime((today - timedelta(days=6)).timetuple())
    timeRanges = [(start, start + 3600*4) for start in range(int(startTime), int(currentTime), 3600*4)]
   
    return aggregateForAllUsers(answerKey, timeRanges, socialForTimeRange)

def recentActivityScore():
    #data = recentActivityLevels(False)
    currentTime = time.time()
    data = aggregateForAllUsers(None, [(currentTime - 3600 * 24 * 7, currentTime)], activityForTimeRange)
    score = {}

    baselineTotal = max([sum([a["total"] for a in activityList]) for uuid, activityList in data.iteritems()])
    totals = { uuid: sum([a["total"] for a in activityList]) for uuid, activityList in data.iteritems()}
    factors = { uuid: (1000.0 / float(total)) if total > 0 else 1 for uuid, total in totals.iteritems()}
    activeTotals = [factors[uuid] * sum([a["low"] + a["high"] for a in activityList]) for uuid, activityList in data.iteritems()]
    activeAverage = sum(activeTotals) / len(activeTotals)
    print activeTotals
    print activeAverage
    variances = [(activeTotal - activeAverage)**2 for activeTotal in activeTotals]
    averageVariance = sum(variances) / len(variances)
    stdDev = math.sqrt(averageVariance)
    print stdDev

    for uuid, activityList in data.iteritems():
        if len(activityList) > 0:
            score[uuid] = computeActivityScore(activityList)

    return score

#def recentActivityScore():
#    data = recentActivityLevels()
#    score = {}
#    
#    for uuid, activityList in data.iteritems():
#        if len(activityList) > 0:
#            score[uuid] = computeActivityScore(activityList)
#    
#    return score

def recentFocusScore():
    data = recentFocusLevels()
    score = {}

    for uuid, focusList in data.iteritems():
        if len(focusList) > 0:
            score[uuid] = computeFocusScore(focusList)
    
    return score

def recentSocialScore():
    data = recentSocialLevels()
    score = {}
    
    for uuid, socialList in data.iteritems():
        if len(socialList) > 0:
            score[uuid] = computeSocialScore(socialList)
    
    return score

def addNotification(profile, notificationType, title, content, uri):    
    notification, created = Notification.objects.get_or_create(datastore_owner=profile, type=notificationType)
    notification.title = title
    notification.content = content
    notification.datastore_owner = profile
    if uri is not None:
        notification.uri = uri
    notification.save()    

def addNotificationAndNotify(profile, notificationType, title, content, uri):
    addNotification(profile, notificationType, title, content, uri)
    if Device.objects.filter(datastore_owner = profile).count() > 0:
        gcm = GCM(settings.GCM_API_KEY)

        for device in Device.objects.filter(datastore_owner = profile):
            try:
                gcm.plaintext_request(registration_id=device.gcm_reg_id, data= {"action":"notify"})
            except Exception as e:
                print "Issue with sending notification to: %s, %s" % (profile.id, profile.uuid)
                print e

def notifyAll():
    for profile in Profile.objects.all():
        if Device.objects.filter(datastore_owner = profile).count() > 0:
            gcm = GCM(settings.GCM_API_KEY)
            for device in Device.objects.filter(datastore_owner = profile):
                try:
                    gcm.plaintext_request(registration_id=device.gcm_reg_id, data={"action":"notify"})
                except Exception as e:
                    print "Issue with sending notification to: %s, %s" % (profile.id, profile.uuid)
                    print e

def broadcastNotification(notificationType, title, content, uri):
    for profile in Profile.objects.all():
        addNotificationAndNotify(profile, notificationType, title, content, uri)

@task() 
def checkDataAndNotify():
    profiles = Profile.objects.all()
    data = {}
    
    currentTime = time.time()
    recentTime = currentTime - 24 * 3600  
    
    for profile in profiles:
        dbName = "User_" + str(profile.id)
        collection = connection[dbName]["funf"]
        newNotifications = False
       
        recentEntries = collection.find({ "time": {"$gte": recentTime }})
        
        if (recentEntries.count() == 0):
            addNotification(profile, 1, "Stale behavioral data", "Analysis may not accurately reflect your behavior.", None)
            newNotifications = True
        #addNotification(profile, 2, "Survey", "Take this survey", "/survey/?survey=1");
        #newNotifications = True
        if (newNotifications and Device.objects.filter(datastore_owner = profile).count() > 0):
            gcm = GCM(settings.GCM_API_KEY)
            #addNotification(profile, 2, "Push successful", "Push notifications are working properly.")
            for device in Device.objects.filter(datastore_owner = profile):
                #pdb.set_trace() 
                gcm.plaintext_request(registration_id=device.gcm_reg_id,data= { "action":"notify" })

@task()
def sendVerificationSurvey():
    broadcastNotification(2, "Social Health Survey", "Please take a moment to complete this social health survey", "/survey/?survey=8")

@task()
def sendPast3DaysSurvey():
    broadcastNotification(2, "Social Health Survey", "Please take a moment to complete this social health survey", "/survey/?survey=5")

@task() 
def sendExperienceSampleSurvey():
    broadcastNotification(2, "Social Health Survey", "Please take a moment to complete this social health survey", "/survey/?survey=9")

@task()
def scheduleTest():
    print "sending..."

def minDiff(elements, item):
    return min([abs(el - item) for el in elements])

@task()
def scheduleExperienceSamplesForToday():
    # We're scheduling 4 surveys / day, starting in the morning, with at least an hour of time in between each
    # assuming we send the first within 2 hours of running this, and need to get all surveys done within 8 hours,
    # we can build the list of delays via simple rejection  
    maxDelay = 3600 * 8 
    delays = [random.randint(0,maxDelay)]
    while len(delays) < 4:
        nextDelay = random.randint(0, maxDelay)
        if minDiff(delays, nextDelay) >= 3600:            
            delays.append(nextDelay)
    print delays
    print [time.strftime("%H:%M", time.localtime(1385042444 + d)) for d in delays]
    for t in delays:
        print "sending survey with %s second delay..." % str(t)
        sendExperienceSampleSurvey.apply_async(countdown = t)
            
@task() 
def ensureFunfIndexes():
    profiles = Profile.objects.all()

    for profile in profiles:
        dbName = "User_" + str(profile.id)
        collection = connection[dbName]["funf"]
        collection.ensure_index([("time", -1), ("key", 1)], cache_for=7200, background=True)

@task()
def recentSocialHealthScores():
    profiles = Profile.objects.all()
    data = {}
    
    activityScores = recentActivityScore()
    socialScores = recentSocialScore()
    focusScores = recentFocusScore()

    scoresList = [activityScores.values(), socialScores.values(), focusScores.values()]
#    scoresList = [[d for d in scoreList if d > 0.0] for scoreList in scoresList]
    averages = [sum(scores) / len(scores) if len(scores) > 0 else 0 for scores in scoresList]
    variances = [map(lambda x: (x - averages[i]) * (x - averages[i]), scoresList[i]) for i in range(len(scoresList))]
    stdDevs = [math.sqrt(sum(variances[i]) / len(scoresList[i])) for i in range(len(scoresList))]

    activityStdDev = stdDevs[0]
    socialStdDev = stdDevs[1]
    focusStdDev = stdDevs[2]
   
    for profile in [p for p in profiles if p.uuid in activityScores.keys()]:
        dbName = "User_" + str(profile.id)
        collection = connection[dbName]["answerlist"]
        
        answer = collection.find({ "key" : "socialhealth" })
        answer = answer[0] if answer.count() > 0 else {"key": "socialhealth", "value":[]} 
        
        #data[profile.uuid] = [datum for datum in answer["value"] if datum["layer"] != "User"]
        data[profile.uuid] = []
        #pdb.set_trace()
        data[profile.uuid].append({ "key": "activity", "layer": "User", "value": activityScores.get(profile.uuid, 0) })
        data[profile.uuid].append({ "key": "social", "layer": "User", "value": socialScores.get(profile.uuid, 0) })
        data[profile.uuid].append({ "key": "focus", "layer": "User", "value": focusScores.get(profile.uuid, 0)  })
        data[profile.uuid].append({ "key": "activity", "layer": "averageLow", "value": max(0, averages[0] - stdDevs[0])})
        data[profile.uuid].append({ "key": "social", "layer": "averageLow", "value": max(0, averages[1] - stdDevs[1]) })
        data[profile.uuid].append({ "key": "focus", "layer": "averageLow", "value": max(0, averages[2] - stdDevs[2]) })
        data[profile.uuid].append({ "key": "activity", "layer": "averageHigh", "value": min(averages[0] + stdDevs[0], 10) })
        data[profile.uuid].append({ "key": "social", "layer": "averageHigh", "value": min(averages[1] + stdDevs[1], 10) })
        data[profile.uuid].append({ "key": "focus", "layer": "averageHigh", "value": min(averages[2] + stdDevs[2], 10) })

        answer["value"] = data[profile.uuid]
        
        collection.save(answer)
    activityLevels = recentActivityLevels()
    return data

def distanceBetweenLatLongs(latlong1, latlong2):  
    earthRadius = 6371 # km
    dLat = math.fabs(math.radians(latlong2[0] - latlong1[0]))
    dLong = math.fabs(math.radians(latlong2[1] - latlong1[1]))
    lat1 = math.radians(latlong1[0])
    lat2 = math.radians(latlong2[0])
    dLatSin = math.sin(dLat / 2.0)
    dLongSin = math.sin(dLong / 2.0)
    a = dLatSin*dLatSin + dLongSin*dLongSin*math.cos(lat1)*math.cos(lat2)
    c = 2 * math.atan2(math.sqrt(a), math.sqrt(1-a))
    return earthRadius * c * 1000 # Converting to meters here... more useful for our purposes than km

def boxContainsPoint(box, point):
    return box[0] <= point[0] <= box[2] and box[1] <= point[1] <= box[3]

def boundsOverlap(bounds1, bounds2):
    points1 = [(x, y) for x in bounds1[::2] for y in bounds1[1::2]]
    points2 = [(x, y) for x in bounds2[::2] for y in bounds2[1::2]]
    
    for point in points1:
        if boxContainsPoint(bounds2, point):
            return True

    for point in points2:
        if boxContainsPoint(bounds1, point):
            return True

    return False

def mergeBoxes(bounds1, bounds2):
    return [min(bounds1[0], bounds2[0]), min(bounds1[1], bounds2[1]), max(bounds1[2], bounds2[2]), max(bounds1[3], bounds2[3])]

def findRecentPlaceBounds(recentPlaceKey, timeRanges):
    profiles = Profile.objects.all()
    data = {}
    
    for profile in profiles:
        dbName = "User_" + str(profile.id)
        collection = connection[dbName]["funf"]
        locations = []

        # An explanation for why we're doing things the way we are below 
        # (there are a few obvious strategies for finding places in location data):
        # 1)  Naive approach - take all location samples in all time ranges, find clusters within them, 
        #     take the one with the most points in it.
        # 2)  Faster, but more complicated - do 1) for each time range individually to get candidate regions. 
        #     Loop over candidate regions, collapsing and "voting" for those that overlap. Take the one with the most votes.
        #     Notes: This is essentially 2-levels of clustering with the simplification that overlapping regions would 
        #     have been clustered together anyway (ie; bounding boxes should be similar, but not the same, as strategy 1)
        #     Pros: Faster - each clustering is limited to 100 entries. In practice, this is more than enough. 
        #         If this poses an issue, time ranges can be chosen more carefully (more / shorter time ranges)
        #     Cons: Bounding boxes aren't the same as 1). In particular, two candidate boxes may not overlap, but should 
        #         have been clustered together anyway.
        # 3)  Binning pre-process - Same as 1), but perform a binning pre-process on the location data, collapsing multiple 
        #     samples into single entries, with associaated weights.
        #     Notes: This is essentially a lower-resolution version of strategy 1. Bounding boxes should be lower-resolution
        #     versions of those from strategy 1. 
        #     Pros: Bounding boxes should be the same as #1. Takes into account all entries when clustering. 
        #     Cons: Less fine-grained control over the number of entries per cluster than #2. In particular, for sparse 
        #         location data, this may not reduce the number of entries we must cluster.
        # The following is an implementation of method #2:
        potentialRegions = []
        #pdb.set_trace()
        for timeRange in timeRanges:
            values = [entry["value"] for entry in collection.find({ "key": { "$regex": "LocationProbe$"}, "time": { "$gte": timeRange[0], "$lt": timeRange[1]}}, limit=100)]
            values = [value["location"] if "location" in value else value for value in values]
            latlongs = [(value["mlatitude"], value["mlongitude"]) for value in values]
            clustering = cluster.HierarchicalClustering(latlongs, distanceBetweenLatLongs)
            clusters = clustering.getlevel(400)

            if (len(clusters) > 0):
                clusterLocations = max(clusters, key= lambda cluster: len(cluster))
                if isinstance(clusterLocations, list):
                    workLats = [loc[0] for loc in clusterLocations]
                    workLongs = [loc[1] for loc in clusterLocations]
                    potentialRegions.append([min(workLats), min(workLongs), max(workLats), max(workLongs)])
                #else:
                #    potentialRegions.append([loc[0], loc[1], loc[0], loc[1]])
        
        if len(potentialRegions) > 0: 
            overlaps = [{ "region" : r1, "overlapList": [r2 for r2 in potentialRegions if r2 is not r1 and boundsOverlap(r1, r2)]} for r1 in potentialRegions]
            mostOverlap = max(overlaps, key = lambda entry: len(entry["overlapList"]))
            mostVoted = reduce(lambda r1, r2: mergeBoxes(r1, r2), mostOverlap["overlapList"], mostOverlap["region"])
            
            answerlistCollection = connection[dbName]["answerlist"]
            answer = answerlistCollection.find({ "key" : "RecentPlaces" })
            answer = answer[0] if answer.count() > 0 else {"key": "RecentPlaces", "value":[]}
            data[profile.uuid] = [datum for datum in answer["value"] if datum["key"] != recentPlaceKey]
            data[profile.uuid].append({ "key": recentPlaceKey, "bounds": mostVoted})
            answer["value"] = data[profile.uuid]
            answerlistCollection.save(answer)
    return data

def findRecentLocations():
    currentTime = time.time()
    today = date.fromtimestamp(currentTime)
    startTime = time.mktime((today - timedelta(days=3)).timetuple())
    sampleTimes = range(int(startTime),int(currentTime), 3600)

    data = {}

    profiles = Profile.objects.all()

    for profile in profiles:
        dbName = "User_" + str(profile.id)
        collection = connection[dbName]["funf"]
        answerlistCollection = connection[dbName]["answerlist"]
        answerlistCollection.remove({"key":"RecentLocations"})
        answer = answerlistCollection.find({ "key": "RecentLocations" })
        answer = answer[0] if answer.count() > 0 else { "key": "RecentLocations", "value":[]}
        data[profile.uuid] = []

        for sampleTime in sampleTimes:
            sampleNumber = int(sampleTime - startTime) / 3600
            locationKey = str(sampleNumber) + "-" + str(sampleNumber + 1)
            values = [entry["value"] for entry in collection.find({ "key": { "$regex": "LocationProbe$"}, "time": { "$gte": sampleTime, "$lt": sampleTime + 3600}}, limit = 100)]
            values = [value["location"] if "location" in value else value for value in values]

            latlongs = [[value["mlatitude"], value["mlongitude"]] for value in values]
    
            if len(latlongs) > 0:
                data[profile.uuid].append({ "key": locationKey, "points": latlongs})
        
        answer["value"] = data[profile.uuid]
        answerlistCollection.save(answer)
    
    return data

@task()
def createUserRecord():
    currentTime = time.time()
    today = date.fromtimestamp(currentTime)
    startTime = time.mktime((today - timedelta(days=1)).timetuple())

    socialHealthScores = recentSocialHealthScores()
    data = {}

    for guid, scores in socialHealthScores.iteritems():
        profile = Profile.objects.get(uuid = guid)
        dbName = "User_" + str(profile.id)
        data[guid] = {}
        collection = connection[dbName]["funf"]
        answerCollection = connection[dbName]["answer"]
        answerCollection.remove()#{"key":"UserRecord"})
        averageLows = {score["key"] : score["value"] for score in scores if score["layer"] == "averageLow"}
        averageHighs = {score["key"] : score["value"] for score in scores if score["layer"] == "averageHigh"}
        userScores = {score["key"] : score["value"] for score in scores if score["layer"] == "User"}
        print averageLows
        print averageHighs
        print userScores
        color = "none"
        color_fill = "#9ACD32"
        for metric, value in userScores.iteritems():
            if value < averageLows[metric] or value > averageHighs[metric]:
                color_fill = "#FF0000"
                color = "red"
        locations = [entry["value"] for entry in collection.find({ "key": { "$regex": "LocationProbe$"}, "time": { "$gte": startTime, "$lt": currentTime}}, limit = 200)]
        locations = [value["location"] if "location" in value else value for value in locations]
        
        timestampedlatlongs = [{ "timestamp":int(value["timestamp"]), "lat":value["mlatitude"], "lng":value["mlongitude"]} for value in locations]
        data[guid]["user"] = "Name"
        data[guid]["color"] = color
        data[guid]["color-fill"] = color_fill
        data[guid]["locations"] = timestampedlatlongs
        data[guid]["issharing"] = False
        data[guid]["photo"] = "/static/img/bbc_demo/locked.png"
        answer = { "key":"UserRecord"}
        answer["value"] = data[guid]
        answerCollection.save(answer)
    return data


@task()
def findRecentPlaces():
    currentTime = time.time()
    today = date.fromtimestamp(currentTime)
    startTime = time.mktime((today - timedelta(days=40)).timetuple())

    # Note: we're not taking the full 9-5 sampling. Clustering is expensive, so anything we can leave out helps...
    # Combined with the fact that "lunch" time might not be indicative of work locations, this might be more accurate anyway       
    nineToFives = [(nine, nine + 3600*8) for nine in range(int(startTime + 3600*9), int(currentTime), 3600*24) if date.fromtimestamp(nine).weekday() < 5]
    #nineToFives.extend([(two, two + 3600*2) for two in range(int(startTime + 3600*14), int(currentTime), 3600*24)])

    
    #print "Finding work locations..."       
    data = findRecentPlaceBounds("work", nineToFives)
    midnightToSixes = [(midnight, midnight + 3600*6) for midnight in range(int(startTime), int(currentTime), 3600* 24) if date.fromtimestamp(midnight).weekday() < 5]

    #print "Finding home locations..."
    data = findRecentPlaceBounds("home", midnightToSixes)

    return data

def getTimeInterval(funf, place, startTime, endTime, workHours = None):
    startTimes = []
    endTimes = []
    for intervalStart in [t for t in range(int(startTime), int(endTime), 3600 * 24) if date.fromtimestamp(t).weekday() < 5]:
        placeLocations = [(entry["time"], entry["value"]) for entry in funf.find({ "key": { "$regex": "LocationProbe$"}, "time": { "$gte": intervalStart, "$lt": intervalStart + 24*3600 }}, limit = 1000)]
        if len(placeLocations) > 0:
            placeLocations = [(value[0], value[1]["location"]) if "location" in value[1] else value for value in placeLocations]
            placeLocations = [(value[0], (value[1]["mlatitude"], value[1]["mlongitude"])) for value in placeLocations]
            placeTimes = [value[0] - intervalStart for value in placeLocations if boxContainsPoint(place["bounds"], value[1])]
            if workHours is None and len(placeTimes) > 0:
                startTimes.append(min(placeTimes))
                endTimes.append(max(placeTimes))
            elif workHours is not None and len([t for t in placeTimes if t < workHours[0]]) > 0  and len([t for t in placeTimes if t > workHours[1]]) > 0:
                endTimes.append(max([t for t in placeTimes if t < workHours[0]]))
                startTimes.append(min([t for t in placeTimes if t > workHours[1]]))
    if len(startTimes) > 0 and len(endTimes) > 0:
        averageStartTime = sum(startTimes) / len(startTimes)
        averageEndTime = sum(endTimes) / len(endTimes)
        return (averageStartTime,  averageEndTime )
    return None

@task()
def estimateTimes():
    profiles = Profile.objects.all()
    currentTime = time.time()
    today = date.fromtimestamp(currentTime)
    startTime = time.mktime((today - timedelta(days=30)).timetuple())

    for profile in profiles:
        dbName = "User_" + str(profile.id)
        answerListCollection = connection[dbName]["answerlist"]
        funf = connection[dbName]["funf"]
        recentPlaces = answerListCollection.find({ "key": "RecentPlaces" })
        if recentPlaces.count() > 0:
            recentPlaces = recentPlaces[0]
            work = [p for p in recentPlaces["value"] if p["key"] == "work"]
            if len(work) > 0:
                work = work[0]
                workInterval = getTimeInterval(funf, work, startTime, currentTime)
                if workInterval is not None:
                    recentPlaces["value"].remove(work)
                    work["start"] = workInterval[0] / 3600
                    work["end"] = workInterval[1] / 3600
                    recentPlaces["value"].append(work)
                    answerListCollection.save(recentPlaces)
            home = [p for p in recentPlaces["value"] if p["key"] == "home"]
            if len(home) > 0 and workInterval is not None:
                home = home[0]
                homeInterval = getTimeInterval(funf, home, startTime, currentTime, workInterval)
                if homeInterval is not None:
                    recentPlaces["value"].remove(home)
                    home["start"] = homeInterval[0] / 3600
                    home["end"] = homeInterval[1] / 3600
                    recentPlaces["value"].append(home)
                    answerListCollection.save(recentPlaces)
