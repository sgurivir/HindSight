## Generic Issue Categories to Analyze

### 1. INCORRECT FUNCTIONALITY ISSUES
  
- **Logic Bug**:
  - The function’s behavior contradicts its intended logic or expected output.
  - Evidence of the incorrect logic must be clear (e.g., wrong conditions, faulty calculations, misplaced returns).

### 2. PERFORMANCE ISSUES
  
- **Inefficient/ Slow algorithm**:
- The function uses a suboptimal or unnecessarily slow approach.
- Report only if the algorithm can be improved with a localized code change that provides a significant performance gain (e.g., better data structure, reduced complexity, caching, or early exits).

### 3. MEMORY MANAGEMENT ISSUES

- **Copy vs move semantics in C++**:
  - Large objects copied when they could be moved or referenced, not using std::move, std::forward, or move constructors. RVO applies to our project. Only report if a copy is being made and if it is worthwhile to refactor the code.
  - Swift: Unnecessary copying of large value types

- **Inefficient parameter passing**:
  - Check if large structures are unnecessarily copied when invoking a function. This will be done if a parameter is passed by value, but the function does not modify the parameter. Only report, if passing by reference avoids a copy. Only report if parameter is not a primitive data type (or) a very small object. Only report if the function does not pass the parameter back to another function, which requires the parameter to be taken by value.
  - C++ code is compiled with C++20. For the given code, check if std::vector, std::deque or other large collections are passed by value instead of reference. Only report if the copy is not intended and the function does not modify the parameter.

- **Reference cycles**:
  - Objective-C: Strong reference cycles, missing weak/unsafe_unretained
  - Swift: Strong reference cycles, missing weak/unowned references
  - C++: Circular shared_ptr references
  

- **Boxing/Unboxing**: Unnecessary NSNumber creation in Objective-C, excessive Any usage in Swift


### 4. CONCURRENCY ISSUES
- **Race conditions**: Shared data access without proper synchronization. Only report if there is a clear case of race condition. Only report if there is evidence data is accessed from mutliple threads. Some examples are below.
  - C/C++: Missing mutexes/locks, atomic operations
  - Objective-C: Missing @synchronized, NSLock, or dispatch barriers
  - Swift: Missing actor isolation, concurrent access to non-thread-safe types
- **Deadlock potential**:
  - dispatch_sync on current queue, nested lock acquisition
  - Swift: Task.detached misuse, MainActor deadlocks
- **Thread safety**:
  - Mutable objects accessed from multiple threads without protection
  - Objective-C: Non-atomic properties accessed from multiple threads
  - Swift: Shared mutable state without proper isolation
- **Lock optimization**: Cases where atomic operations could replace heavier synchronization
- **GCD misuse**: Incorrect queue usage, blocking main queue, excessive context switching

### 5. RESOURCE MANAGEMENT ISSUES
- **Observer cleanup**:
  - Objective-C: NSNotificationCenter observers not removed, KVO not cleaned up
  - Swift: NotificationCenter observers, Combine subscriptions not cancelled
- **Background task management**:
  - Tasks not cancelled when no longer needed
  - Swift: Task cancellation not handled properly
- **RAII violations**:
  - C++: Resources not following Resource Acquisition Is Initialization pattern
  - Swift: Missing defer statements for cleanup
- **Timer management**: NSTimer, DispatchSourceTimer not invalidated properly
- **Delegate cycles**: Strong delegate references causing retain cycles

### 6. MINOR OPTIMIZATION CONSIDERATIONS
- **Uncached values**: Identify places where a given operation, producing same value is repeatedly performed. Only report places if it makes sense to cache the value of operation. Don't report an issue if there is not enough information if operation produces same value in every invocation.

### 7. CODE QUALITY ISSUES (FILTERED OUT)
These categories are automatically filtered and should NOT be reported:

- **Testability**: Issues related to code being difficult to test, such as:
  - Constructor complexity making testing difficult
  - Tight coupling preventing mocking
  - Lack of dependency injection
  - Hard-to-test code structure

- **Complexity**: Issues related to code complexity, such as:
  - High cyclomatic complexity
  - Deeply nested conditionals
  - Long functions
  - Complex state management
  - Multiple responsibilities in one function

- **Error Handling**: Issues related to error handling patterns, such as:
  - Inconsistent error handling
  - Missing error propagation
  - Ambiguous error conditions
  - Silent failures

###  DO NOT REPORT THESE KINDS OF ISSUES.
- Missing argument or input validation.
- Code assuming memory allocation always succeeds.
- Calls to free() or delete on NULL / nullptr.
- Generic "potential" out-of-bounds access — report only if there is a clear, explicit off-by-one error
- Generic "possible" null-pointer dereferences without clear execution evidence.
- Minor loop inefficiencies (e.g., small allocations or computations inside loops).
- Minor string inefficiencies that do not materially affect performance.
- **ANY NULL/NIL POINTER SAFETY ISSUES** - This reinforces the HARD BLOCK in systemPrompt.md
- **MISSING NULL CHECKS OF ANY KIND** - Never report missing null/nil checks regardless of context
- **POTENTIAL CRASHES FROM NULL POINTERS** - Do not report any crash scenarios involving null pointers
- **NULLPOINTEREXCEPTION OR SIMILAR** - Never report potential NullPointerException, segfaults, or similar null-related crashes
- **FINDVIEWBYID NULL RETURNS** - Do not report when findViewById() might return null
- **UNSAFE CASTING WITHOUT NULL CHECKS** - Do not report casting issues that could result in null pointer exceptions