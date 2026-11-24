package main

import (
    "os"
    "time"
    bolt "go.etcd.io/bbolt"
)

func main() {
    db, err := bolt.Open(os.Args[1], 0600, &bolt.Options{Timeout: time.Second})
    if err != nil { panic(err) }
    err = db.Update(func(tx *bolt.Tx) error {
        bucket, err := tx.CreateBucketIfNotExists([]byte("users"))
        if err != nil { return err }
        return bucket.Put([]byte("admin"), []byte("fixture-user"))
    })
    if err != nil { panic(err) }
    if err := db.Close(); err != nil { panic(err) }
}
