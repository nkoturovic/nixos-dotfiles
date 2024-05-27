#!/bin/python3

import sys
import os
import glob

usage = os.path.basename(sys.argv[0]) + " src_dir dest_dir"

if len(sys.argv) != 3:
    sys.exit("Usage: " + usage)

srcDir = sys.argv[1];
destDir = sys.argv[2];

if not os.path.exists(destDir):
    os.makedirs(destDir)

listOfFiles = []

# geting all file path(s)
for filepath in glob.iglob(srcDir + "**/*", recursive=True):
    listOfFiles.append(filepath)

listOfFiles.sort()

# tree file creating
os.system("tree " + srcDir + "> " + destDir + "/000tree.txt")
fileCounter = 1

for filepath in listOfFiles:

    fileName = os.path.basename(filepath)

    if os.path.isfile(filepath): 
        try:
            with open(filepath, "r") as fr:

                # setting header

                fileContent = ""
                filePrepend = ""

                if filePrepend != "":
                    fileContent += filePrepend
                    filePrepend = ""

                fileContent += "/* FILE: " + filepath + " */\n\n"

                # Reading from file
                fileContent += fr.read()

                # setting footer
                fileContent += "\n/* END OF FILE: " + filepath + " */"

                # open file and write to it 
                fw = open(destDir + "/" + "{0:0=3d}".format(fileCounter) + fileName + ".txt", "w")
                fw.write(fileContent)
                fw.close()
                fileCounter += 1

                # increment file enumeration counter

        except UnicodeDecodeError:
            pass
    elif os.path.isdir(filepath):
                filePrepend = "/*****************" + "*" * len(filepath) + "****************/\n"
                filePrepend += "/************* " + "DIR: " + filepath + " *************/\n"
                filePrepend += "/*****************" + "*" * len(filepath) + "****************/\n\n"
